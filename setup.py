from setuptools import setup, find_namespace_packages

NAME = 'interaktiv.kyra'
DESCRIPTION = 'KYRA integration for Plone.'
URL = 'https://github.com/interaktivgmbh/interaktiv.kyra'
EMAIL = 'support@interaktiv.de'
AUTHOR = 'Interaktiv GmbH'
REQUIRES_PYTHON = '>=3.11'
VERSION = '2.2.4'
REQUIRED = [
    'setuptools',
    'Plone>=6.0',
    'PyPDF2',
    'pytesseract',
    'Pillow',
    'pdf2image',
    'striprtf',
    'pdfminer.six',
    'deepl',
    'passlib',
    'langchain>=1.2',
    'langchain-openai>=1.1',
    'langchain-core>=1.2',
    'langgraph>=1.0',
    'langchain-text-splitters>=0.3',
    'httpx',
]
EXTRAS = {
    'test': ['plone.app.testing']
}

setup(
    name=NAME,
    version=VERSION,
    description=DESCRIPTION,
    long_description=DESCRIPTION,
    long_description_content_type='text/markdown',
    classifiers=[
        "Environment :: Web Environment",
        "Framework :: Plone",
        "Framework :: Plone :: Addon",
        "Operating System :: OS Independent",
        "Programming Language :: Python",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Topic :: Software Development :: Libraries :: Python Modules",
    ],
    keywords='plone kyra ai llm',
    author=AUTHOR,
    author_email=EMAIL,
    url=URL,
    license='GPL version 2',
    packages=find_namespace_packages(where='src'),
    package_dir={'': 'src'},
    include_package_data=True,
    zip_safe=False,
    python_requires=REQUIRES_PYTHON,
    install_requires=REQUIRED,
    extras_require=EXTRAS,
    entry_points="""
    [z3c.autoinclude.plugin]
    target = plone
    """
)
