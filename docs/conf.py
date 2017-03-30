import os
import sys
import django  # noqa

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

sys.path.insert(0, ROOT_DIR)
sys.path.insert(1, os.path.abspath('.'))
os.environ['DJANGO_SETTINGS_MODULE'] = 'msa_rcalendar.settings'

django.setup()

source_parsers = {
   '.md': 'recommonmark.parser.CommonMarkParser',
}

extensions = ['sphinx.ext.autodoc']
templates_path = ['_templates']
source_suffix = ['.rst', '.md']
master_doc = 'index'
project = 'msa_rcalendar'
version = '0.1'
release = '0.1'
language = 'ru'
exclude_patterns = ['_build', 'Thumbs.db', '.DS_Store']
pygments_style = 'sphinx'
todo_include_todos = False
html_theme = 'sphinx_rtd_theme'
html_static_path = ['_static']
htmlhelp_basename = 'msa_rcalendardoc'
latex_elements = {}
latex_documents = []
man_pages = []
texinfo_documents = []
