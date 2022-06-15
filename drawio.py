from urllib.parse import quote, unquote
import os
import xml.etree.ElementTree as ET
import zlib
import base64

XML_FILEPATH = 'drawio.xml'
PNG_FILEPATH = 'drawio.png'

DRAW_IO_CONVERT_XML_TO_PNG_CMD = f'/Applications/draw.io.app/Contents/MacOS/draw.io -x -f png -e {XML_FILEPATH}'


class DrawioConverter:
    @classmethod
    def convert_xml_to_base64_png(cls, xml):
        if not cls.__create_xml_file(xml):
            return

        # TODO: Move xml to png convertation to this class to avoid relying on `draw.io` app
        os.system(DRAW_IO_CONVERT_XML_TO_PNG_CMD)

        try:
            base64_png = base64.b64encode(open(PNG_FILEPATH, 'rb').read()).decode('utf-8')
        except Exception as e:
            print(f"Failed to convert XML to PNG, error - {e}")
            base64_png = None
        finally:
            cls.__remove_created_files()
            return base64_png

    @classmethod
    def __create_xml_file(cls, xml):
        try:
            with open(XML_FILEPATH, 'w') as file:
                file.write(cls.__mx_graph_model(xml))
        except Exception as e:
            print(f"Failed to create XML file, error - {e}")
            return False

        return True

    @classmethod
    def __remove_created_files(cls):
        filepaths_to_delete = [filepath for filepath in [XML_FILEPATH, PNG_FILEPATH] if os.path.exists(filepath)]
        for filepath in filepaths_to_delete:
            os.remove(filepath)

    # https://stackoverflow.com/a/69975753
    @classmethod
    def __mx_graph_model(cls, xml):
        uri_decoded_data = cls.__js_decode_uri_component(xml)

        ## Extract diagram data from resulting XML
        root = ET.fromstring(uri_decoded_data)
        diagram_data = next(el.text for el in root if el.tag == 'diagram')

        ## Decode Base64
        diagram_data = cls.__js_atob(diagram_data)
        decompressed_diagram_data = cls.__pako_inflate_raw(diagram_data)

        ## Turn decompressed data into a usable string
        string_diagram_data = cls.__js_bytes_to_string(decompressed_diagram_data)
        string_diagram_data = cls.__js_decode_uri_component(string_diagram_data)

        return string_diagram_data

    @classmethod
    def __js_decode_uri_component(cls, data):
        return unquote(data)

    @classmethod
    def __js_bytes_to_string(cls, data):
        return data.decode('iso-8859-1')

    @classmethod
    def __js_atob(cls, data):
        return base64.b64decode(data)

    @classmethod
    def __pako_inflate_raw(cls, data):
        decompress = zlib.decompressobj(-15)

        decompressed_data = decompress.decompress(data)
        decompressed_data += decompress.flush()

        return decompressed_data
