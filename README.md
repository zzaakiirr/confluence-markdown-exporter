### A very simple Confluence to Markdown exporter.

This code is not written with security in mind, do NOT run it on a repository that can contain mailicious
page titles.


### Usage
1. Install requirements: <code>pip3 install -r requirements.txt</code>
2. Update cookies.json
3. Run the script: <code>python3.9 confluence-markdown-export.py gitlab_wikis_path url space_key out_dir</code>
   providing URL e.g. https://YOUR_PROJECT.atlassian.net, path to gitlab wikis, space to import,
   and output directory, e.g. ./output_dir

The secret token can be generated under Profile -> Security -> Manage API Tokens

### Cookies
Due to using LDAP auth in confluence server it was decided to use cookies as authentication method for interacting with Confluence REST API

For getting `JSESSIONID` value:
1. Authorize in Confluence server via LDAP
2. Make GET request https://YOUR_PROJECT.atlassian.net/wiki/rest/api/space and check Cookies
