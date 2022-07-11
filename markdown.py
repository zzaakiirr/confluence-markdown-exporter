from markdownify import MarkdownConverter


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
