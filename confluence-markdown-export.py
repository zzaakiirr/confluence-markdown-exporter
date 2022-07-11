import os
import json
import argparse
import urllib.parse
from urllib.parse import urlparse, parse_qs, unquote

import requests
import bs4
from markdownify import MarkdownConverter
from atlassian import Confluence, utils
from atlassian.errors import ApiError


ATTACHMENT_FOLDER_NAME = "attachments"

NONCONVERTIBLE_TAGS = [
    'div.attachment-buttons',
    'a.download-all-link',
    'div.plugin_attachments_upload_container',
    'style',
    'img.waiting-image',
    'img.emoticon',
    'img.confluence-external-resource',
]


class ExportException(Exception):
    pass


def parse_cookies():
    with open('cookies.json') as json_file:
        data = json.load(json_file)

    return data


def sanitize_filename(document_name_raw):
    document_name = document_name_raw

    for invalid in ["\\", "/"]:
        if invalid in document_name:
            print("Dangerous page title: \"{}\", \"{}\" found, replacing it with \"_\"".format(
                document_name,
                invalid))
            document_name = document_name.replace(invalid, "_")

    document_name = ' '.join(document_name.split()) # replace multiple whitespaces with single one

    return document_name


class SkipTableMarkdownConverter(MarkdownConverter):
    def process_tag(self, node, convert_as_inline, children_only=False):
        if node.name != 'table':
            return super(SkipTableMarkdownConverter, self).process_tag(node, convert_as_inline, children_only)

        for el in node.find_all():
            del el['class']

        text = str(node.prettify())

        for img in node.find_all('img'):
            converted_img = self.convert_img(img, '', convert_as_inline)
            text = text.replace(str(img), converted_img)

        return f'\n\n{text}\n\n'


class Exporter:
    def __init__(self, url, space_key, out_dir, no_attach):
        self.__out_dir = out_dir
        self.__url = url
        self.__space_key = space_key
        self.__cookies = parse_cookies()
        self.__confluence = Confluence(url=self.__url, cookies=self.__cookies)
        self.__seen = set()
        self.__no_attach = no_attach

    def __parse_cookie_file(self, filepath):
        with open(filepath) as json_file:
            data = json.load(json_file)

        return data

    def __dump_page(self, src_id, parents):
        if src_id in self.__seen:
            # this could theoretically happen if Page IDs are not unique or there is a circle
            raise ExportException("Duplicate Page ID Found!")

        page = self.__confluence.get_page_by_id(src_id, expand="body.export_view")
        page_title = page["title"]
        page_id = page["id"]
    
        # see if there are any children
        child_ids = self.__confluence.get_child_id_list(page_id)
    
        content = page["body"]["export_view"]["value"]

        # save all files as .html for now, we will convert them later
        extension = ".html"
        if len(child_ids) > 0:
            document_name = page_title + extension
        else:
            document_name = page_title + extension

        # make some rudimentary checks, to prevent trivial errors
        sanitized_filename = sanitize_filename(document_name)

        page_location = parents + [sanitized_filename]
        page_filename = os.path.join(self.__out_dir, *page_location)

        page_output_dir = os.path.dirname(page_filename)
        os.makedirs(page_output_dir, exist_ok=True)
        print("Saving to {}".format(" / ".join(page_location)))
        with open(page_filename, "w") as f:
            f.write(content)

        # fetch attachments unless disabled
        if not self.__no_attach:
            ret = self.__confluence.get_attachments_from_content(page_id, start=0, limit=500, expand=None,
                                                                 filename=None, media_type=None)
            for i in ret["results"]:
                att_title = i["title"]
                download = i["_links"]["download"]

                # att_url = self.__url + "wiki/" + download
                att_url = self.__url + download
                att_sanitized_name = sanitize_filename(att_title)
                att_filename = os.path.join(page_output_dir, ATTACHMENT_FOLDER_NAME, att_sanitized_name)

                att_dirname = os.path.dirname(att_filename)
                os.makedirs(att_dirname, exist_ok=True)

                print("Saving attachment {} to {}".format(att_title, page_location))

                r = requests.get(att_url, stream=True, cookies=self.__cookies)
                r.raise_for_status()
                with open(att_filename, "wb") as f:
                    f.write(r.content)

        self.__seen.add(page_id)
    
        # recurse to process child nodes
        for child_id in child_ids:
            try:
                self.__dump_page(child_id, parents=parents + [sanitize_filename(page_title)])
            except:
                print(f"Failed to dump child - {child_id}")
                continue
    
    def dump(self):
        space = self.__confluence.get_space(self.__space_key, expand='description.plain,homepage')
        print("Processing space", self.__space_key)
        if space.get("homepage") is None:
            print("Skipping space: {}, no homepage found!".format(self.__space_key))
            print("In order for this tool to work there has to be a root page!")
            raise ExportException("No homepage found")
        else:
            # homepage found, recurse from there
            homepage_id = space["homepage"]["id"]
            self.__dump_page(homepage_id, parents=[self.__space_key])


class Converter:
    def __init__(self, out_dir, gitlab_wikis_path, url):
        self.__out_dir = out_dir
        self.gitlab_wikis_path = gitlab_wikis_path
        self.__confluence = Confluence(url=url, cookies=parse_cookies())

    def recurse_findfiles(self, path):
        for entry in os.scandir(path):
            if entry.is_dir(follow_symlinks=False):
                yield from self.recurse_findfiles(entry.path)
            elif entry.is_file(follow_symlinks=False):
                yield entry
            else:
                raise NotImplemented()

    def __convert_html(self, soup):
        soup = self.__extract_nonconvertible_tags(soup)
        soup = self.__convert_attachments(soup)
        soup = self.__convert_jira_issues(soup)
        soup = self.__convert_drawio_diagrams(soup)
        soup = self.__convert_page_links(soup)
        soup = self.__convert_user_links(soup)

        return soup

    def __extract_nonconvertible_tags(self, soup):
        for tag in soup.select(','.join(NONCONVERTIBLE_TAGS)):
            tag.extract()

        return soup

    def __convert_user_links(self, soup):
        user_links = [anchor for anchor in soup.find_all('a', {'class': 'confluence-userlink'})]

        for user_link in user_links:
            user_reference = soup.new_tag('span')
            user_reference.string = f"@{user_link['data-username']}"
            user_link.replace_with(user_reference)

        return soup

    def __convert_drawio_diagrams(self, soup):
        drawio_imgs = [img for img in soup.find_all('img') if 'data:image/png;base64' in img.get('src', '')]

        for drawio_img in drawio_imgs:
            drawio_img['alt'] = 'diagram'

        return soup

    def __convert_attachments(self, soup):
        attachment_links = [
            anchor for anchor in soup.select('a:not(.external-link)') if 'download/attachments/' in anchor.get('href', '')
        ]

        for attachment_link in attachment_links:
            attachment_name = attachment_link.get('data-linked-resource-default-alias') or \
                              attachment_link.get('data-filename')

            if not attachment_name:
                continue

            attachment_name = sanitize_filename(attachment_name)
            src = os.path.join(ATTACHMENT_FOLDER_NAME, attachment_name)
            img = soup.new_tag('img', attrs={'src': src, 'alt': attachment_name})

            attachment_link.replace_with(img)

        attachment_preview_links = [
            anchor for anchor in soup.select('a:not(.external-link)') if 'preview=' in anchor.get('href', '')
        ]

        for attachment_preview_link in attachment_preview_links:
            query = unquote(urlparse(attachment_preview_link['href']).query)

            attachment_name = query.rpartition('/')[-1].replace('+', ' ')
            attachment_name = sanitize_filename(attachment_name)

            src = os.path.join(ATTACHMENT_FOLDER_NAME, attachment_name)
            img = soup.new_tag('img', attrs={'src': src, 'alt': attachment_name})

            attachment_preview_link.replace_with(img)

        attachment_imgs = [
            img for img in soup.select('img') if 'download/attachments/' in img.get('src', '')
        ]

        for img in attachment_imgs:
            attachment_name = unquote(urlparse(img['src']).path.rpartition('/')[-1])
            attachment_name = sanitize_filename(attachment_name)

            img['alt'] = attachment_name
            img['src'] = os.path.join(ATTACHMENT_FOLDER_NAME, attachment_name)

        return soup

    def __convert_jira_issues(self, soup):
        jira_issue_spans = [span for span in soup.select('span.jira-issue')]

        for span in jira_issue_spans:
            img = soup.new_tag('img', attrs={'src': span['data-jira-key'], 'alt': 'jira_issue'})
            span.replace_with(img)

        return soup

    def __convert_page_links(self, soup):
        page_links = [anchor for anchor in soup.find_all('a') if 'pages/viewpage.action?' in anchor.get('href', '')]

        for page_link in page_links:
            url = urlparse(page_link['href'])
            page_id = parse_qs(url.query).get('pageId', [None])[0]

            if page_id is None:
                continue

            try:
                page = self.__confluence.get_page_by_id(page_id, expand="ancestors")
            except ApiError:
                page_link['href'] = ''
                continue

            parent_slug = ''
            for parent in page['ancestors']:
                parent_slug += f"/{urllib.parse.quote(sanitize_filename(parent['title']))}" if parent.get('title') else ''

            page_link['href'] = f"{self.gitlab_wikis_path}{parent_slug}/{urllib.parse.quote(sanitize_filename(page['title']))}"
            del page_link['title']

        return soup

    def convert(self):
        for entry in self.recurse_findfiles(self.__out_dir):
            path = entry.path

            if not path.endswith(".html"):
                continue

            print("Converting {}".format(path))
            with open(path) as f:
                data = f.read()

            soup_raw = bs4.BeautifulSoup(data, 'html.parser')
            soup = self.__convert_html(soup_raw)

            md = SkipTableMarkdownConverter().convert_soup(soup)
            newname = os.path.splitext(path)[0]
            with open(newname + ".md", "w") as f:
                f.write(md)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("gitlab_wikis_path", type=str, help="The path to the Gitlab wikis")
    parser.add_argument("url", type=str, help="The url to the confluence instance")
    parser.add_argument("space_key", type=str, help="Space key to import")
    parser.add_argument("out_dir", type=str, help="The directory to output the files to")
    parser.add_argument("--skip-attachments", action="store_true", dest="no_attach", required=False,
                        default=False, help="Skip fetching attachments")
    parser.add_argument("--no-fetch", action="store_true", dest="no_fetch", required=False,
                        default=False, help="This option only runs the markdown conversion")
    args = parser.parse_args()
    
    if not args.no_fetch:
        dumper = Exporter(url=args.url, space_key=args.space_key, out_dir=args.out_dir, no_attach=args.no_attach)
        dumper.dump()
    
    converter = Converter(out_dir=args.out_dir, gitlab_wikis_path=args.gitlab_wikis_path, url=args.url)
    converter.convert()
