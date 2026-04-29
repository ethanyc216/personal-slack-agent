from personal_slack_agent.generated_files import extract_generated_files, normalize_slack_markdown


def test_extract_generated_files_splits_summary_and_files():
    text = """
Use this set as a repo-local starter package.

**`scripts/shepherd/README.md`**
```md
# Shepherd
Hello
```

**`scripts/shepherd/run.sh`**
```bash
echo hi
```
""".strip()

    summary, files = extract_generated_files(text)

    assert summary == "Use this set as a repo-local starter package."
    assert [(item.path, item.content) for item in files] == [
        ("scripts/shepherd/README.md", "# Shepherd\nHello"),
        ("scripts/shepherd/run.sh", "echo hi"),
    ]


def test_extract_generated_files_leaves_normal_text_untouched():
    text = "Plain answer with no file sections."

    summary, files = extract_generated_files(text)

    assert summary == text
    assert files == []


def test_extract_generated_files_supports_bullet_and_colon_file_headers():
    text = """
Here is a shepherd skill set.

- **`skills/shepherd/SKILL.md`**:
```md
# Shepherd Deploy
Use this skill.
```

- **`skills/shepherd/checklist.txt`**:
```text
step one
step two
```
""".strip()

    summary, files = extract_generated_files(text)

    assert summary == "Here is a shepherd skill set."
    assert [(item.path, item.content) for item in files] == [
        ("skills/shepherd/SKILL.md", "# Shepherd Deploy\nUse this skill."),
        ("skills/shepherd/checklist.txt", "step one\nstep two"),
    ]


def test_normalize_slack_markdown_strips_fence_language():
    text = """
Before
```text
https://confluence.example.com/confluence/display/DOPE/How+to+make+API+calls+with+Request+Signing
```
After
""".strip()

    assert normalize_slack_markdown(text) == """
Before
```
https://confluence.example.com/confluence/display/DOPE/How+to+make+API+calls+with+Request+Signing
```
After
""".strip()


def test_normalize_slack_markdown_strips_indented_fence_language():
    text = """
Here:
    ```text
    https://confluence.example.com/confluence/display/DOPE/How+to+make+API+calls+with+Request+Signing
    ```
""".strip()

    assert normalize_slack_markdown(text) == """
Here:
    ```
    https://confluence.example.com/confluence/display/DOPE/How+to+make+API+calls+with+Request+Signing
    ```
""".strip()
