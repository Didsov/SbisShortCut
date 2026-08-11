import os


# Авторизационные Cookie никогда не хранятся в исходном коде.
COOKIES = os.environ.get("SBIS_COOKIES", "").strip()

