from personal_slack_agent.generated_files import extract_generated_files


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
