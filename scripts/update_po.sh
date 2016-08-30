#!/bin/bash
./manage.py makemessages -l ru --no-obsolete
./manage.py compilemessages
echo "Done"
