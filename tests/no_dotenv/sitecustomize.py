"""Test-process bootstrap: never read local credentials, including child Python processes."""
import os

if os.environ.get('REGULATORY_LOCAL_TEST') == '1':
    import dotenv
    import dotenv.main

    def disabled_dotenv(*args, **kwargs):
        return False

    dotenv.load_dotenv = disabled_dotenv
    dotenv.main.load_dotenv = disabled_dotenv
