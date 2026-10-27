# AI Assistant (Gemini) Instructions

When working with this repository, always adhere to the following rules:

1. **Do not edit `config/test.env` or `config/prod.env`.** These files are strictly for local user/operator configuration and must never be modified programmatically or manually during AI sessions.
2. **All file contents must be in English.** This includes code, comments, documentation, and commit messages. Do not use any other language when writing or modifying files.
3. **Environment files generation:** `config/prod.env` and `config/test.env` must be generated from their respective templates (`config/prod.env.example` and `config/test.env.example`). Do not populate them manually; `config/prod.env` is populated by calling the `prod-env-host` script, and `config/test.env` by `test-env-host`.
