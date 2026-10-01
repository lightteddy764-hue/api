#!/usr/bin/env bash
# render-build.sh
pip install -r requirements.txt
playwright install chromium --with-deps
