import json


COOKIES_FILEPATH = 'cookies.json'


def parse_cookies():
    with open(COOKIES_FILEPATH) as json_file:
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
