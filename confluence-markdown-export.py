import os
import argparse

import requests
import bs4
from markdownify import MarkdownConverter
from atlassian import Confluence


# https://github.com/matthewwithanm/python-markdownify/issues/61
class AlwaysRenderImagesConverter(MarkdownConverter):
    def convert_img(self, el, text: str, convert_as_inline: bool) -> str:
        """Allows images to be rendered in headings and table cells"""
        alt = el.attrs.get("alt", None) or ""
        src = el.attrs.get("src", None) or ""
        title = el.attrs.get("title", None) or ""
        title_part = ' "%s"' % title.replace('"', r"\"") if title else ""

        return "![%s](%s%s)" % (alt, src, title_part)


ATTACHMENT_FOLDER_NAME = "attachments"
NON_CONVETIBLE_TAGS = ['colour', 'height', 'weight']


class ExportException(Exception):
    pass


class Exporter:
    def __init__(self, url, username, token, out_dir, no_attach):
        self.__out_dir = out_dir
        self.__url = url
        self.__username = username
        self.__token = token
        self.__confluence = Confluence(url=self.__url, username=self.__username, password=self.__token)
        self.__seen = set()
        self.__no_attach = no_attach

    def __sanitize_filename(self, document_name_raw):
        document_name = document_name_raw
        for invalid in ["..", "/"]:
            if invalid in document_name:
                print("Dangerous page title: \"{}\", \"{}\" found, replacing it with \"_\"".format(
                    document_name,
                    invalid))
                document_name = document_name.replace(invalid, "_")
        return document_name

    def __dump_page(self, src_id, parents):
        if src_id in self.__seen:
            # this could theoretically happen if Page IDs are not unique or there is a circle
            raise ExportException("Duplicate Page ID Found!")

        page = self.__confluence.get_page_by_id(src_id, expand="body.storage")
        page_title = page["title"]
        page_id = page["id"]
    
        # see if there are any children
        child_ids = self.__confluence.get_child_id_list(page_id)
    
        content = page["body"]["storage"]["value"]

        # save all files as .html for now, we will convert them later
        extension = ".html"
        if len(child_ids) > 0:
            document_name = page_title + extension
        else:
            document_name = page_title + extension

        # make some rudimentary checks, to prevent trivial errors
        sanitized_filename = self.__sanitize_filename(document_name)

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

                att_url = self.__url + download # @pv: delete `wiki/` path, otherwise getting 404
                att_sanitized_name = self.__sanitize_filename(att_title)
                att_filename = os.path.join(page_output_dir, ATTACHMENT_FOLDER_NAME, att_sanitized_name)

                att_dirname = os.path.dirname(att_filename)
                os.makedirs(att_dirname, exist_ok=True)

                print("Saving attachment {} to {}".format(att_title, page_location))

                r = requests.get(att_url, auth=(self.__username, self.__token), stream=True)
                r.raise_for_status()
                with open(att_filename, "wb") as f:
                    f.write(r.content) # @pv: using `r.content` instead of using `r.iter_content()`

        self.__seen.add(page_id)
    
        # recurse to process child nodes
        for child_id in child_ids:
            self.__dump_page(child_id, parents=parents + [page_title])
    
    def dump(self):
        ret = self.__confluence.get_all_spaces(start=0, limit=500, expand='description.plain,homepage')
        for space in ret["results"]:
            space_key = space["key"]
            print("Processing space", space_key)
            if space.get("homepage") is None:
                print("Skipping space: {}, no homepage found!".format(space_key))
                print("In order for this tool to work there has to be a root page!")
                raise ExportException("No homepage found")
            else:
                # homepage found, recurse from there
                homepage_id = space["homepage"]["id"]
                self.__dump_page(homepage_id, parents=[space_key])


class Converter:
    def __init__(self, out_dir):
        self.__out_dir = out_dir

    def recurse_findfiles(self, path):
        for entry in os.scandir(path):
            if entry.is_dir(follow_symlinks=False):
                yield from self.recurse_findfiles(entry.path)
            elif entry.is_file(follow_symlinks=False):
                yield entry
            else:
                raise NotImplemented()

    def __convert_atlassian_html(self, soup):
        soup = self.__convert_attachments(soup)
        soup = self.__convert_jira_links(soup)
        soup = self.__extract_nonconvertible_tags(soup)

        return soup

    def __convert_jira_links(self, soup):
        for jira_macro in soup.find_all('ac:structured-macro', {'ac:name': 'jira'}):
            issue_key = jira_macro.find('ac:parameter', {'ac:name': 'key'})

            if issue_key is None:
                continue

            img_tag = soup.new_tag('img', attrs={'src': issue_key.text, 'alt': 'jira_link'})
            jira_macro.replace_with(img_tag)

        return soup

    def __extract_nonconvertible_tags(self, soup):
        for name in NON_CONVETIBLE_TAGS:
            for param in soup.find_all('ac:parameter', {'ac:name': name}):
                param.extract()

        return soup

    def __convert_attachments(self, soup):
        for attachment in soup.find_all(["ac:image", "ri:attachment"]):
            url = attachment.get("ri:filename") or next(child.get("ri:filename", None) for child in attachment.children)

            if url is None:
                # no url found for ac:image, ri:attachment
                continue

            # construct new, actually valid HTML tag
            srcurl = os.path.join(ATTACHMENT_FOLDER_NAME, url)
            imgtag = soup.new_tag("img", attrs={"src": srcurl, "alt": srcurl})

            # insert a linebreak after the original "ac:image", "ri:attachment" tag, then replace with an actual img tag
            attachment.insert_after(soup.new_tag("br"))
            attachment.replace_with(imgtag)
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
            soup = self.__convert_atlassian_html(soup_raw)

            md = AlwaysRenderImagesConverter().convert_soup(soup)
            newname = os.path.splitext(path)[0]
            with open(newname + ".md", "w") as f:
                f.write(md)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("url", type=str, help="The url to the confluence instance")
    parser.add_argument("username", type=str, help="The username")
    parser.add_argument("token", type=str, help="The access token to Confluence")
    parser.add_argument("out_dir", type=str, help="The directory to output the files to")
    parser.add_argument("--skip-attachments", action="store_true", dest="no_attach", required=False,
                        default=False, help="Skip fetching attachments")
    parser.add_argument("--no-fetch", action="store_true", dest="no_fetch", required=False,
                        default=False, help="This option only runs the markdown conversion")
    args = parser.parse_args()
    
    if not args.no_fetch:
        dumper = Exporter(url=args.url, username=args.username, token=args.token, out_dir=args.out_dir,
                          no_attach=args.no_attach)
        dumper.dump()
    
    converter = Converter(out_dir=args.out_dir)
    converter.convert()
