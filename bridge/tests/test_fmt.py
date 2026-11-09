from harness_discord import fmt


def test_short_text_is_one_message():
    assert fmt.chunk("hello") == ["hello"]


def test_empty():
    assert fmt.chunk("  \n ") == []


def test_long_text_is_split_under_the_limit():
    text = "\n\n".join(f"Paragraph {i} " + "word " * 60 for i in range(30))
    parts = fmt.chunk(text)
    assert len(parts) > 1
    assert all(len(p) <= fmt.LIMIT for p in parts)
    assert "Paragraph 0" in parts[0] and "Paragraph 29" in parts[-1]


def test_code_fence_stays_balanced_across_a_cut():
    code = "```python\n" + "\n".join(f"x{i} = {i}" for i in range(700)) + "\n```"
    parts = fmt.chunk("intro\n\n" + code)
    assert len(parts) > 1
    for p in parts:
        assert p.count("```") % 2 == 0
        assert len(p) <= fmt.LIMIT
    assert parts[1].startswith("```python")


def test_table_becomes_bullets():
    t = "| Nom | Valeur |\n|---|---|\n| a | 1 |\n| b | 2 |\n\nfin"
    out = fmt.tables_to_bullets(t)
    assert "- **Nom**: a · **Valeur**: 1" in out and "- **Nom**: b · **Valeur**: 2" in out and out.endswith("fin")


def test_table_inside_code_is_kept():
    t = "```\n| a | b |\n|---|---|\n| 1 | 2 |\n```"
    assert fmt.tables_to_bullets(t) == t


def test_clip():
    assert fmt.clip("a  b\nc", 10) == "a b c"
    assert fmt.clip("x" * 50, 10).endswith("…") and len(fmt.clip("x" * 50, 10)) == 10
