#!/usr/bin/env python
from pygments import highlight
from pygments.lexers import PythonLexer
from pygments.formatters import RtfFormatter

with open("script.py") as in_file:
  with open("script.rtf", 'w') as out:
    out.write(highlight(in_file.read(), PythonLexer(), RtfFormatter()))
