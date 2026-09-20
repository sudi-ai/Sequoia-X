"""V7 experimental/shadow package.

Importing the package only loads local environment configuration. It does not
start network calls and does not alter V6 decisions.
"""
from .env_loader import load_project_env

ENV_LOAD_RESULT = load_project_env()
