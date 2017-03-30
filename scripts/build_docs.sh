#!/bin/bash
./manage.py generate_api_docs --project-name="API Reference" > docs/api_docs.md --settings=msa_rcalendar.docs_settings
cd docs && make html
