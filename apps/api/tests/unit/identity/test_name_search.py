from app.identity.name_search import name_initials, name_matches_query


def test_name_initials_support_common_chinese_names() -> None:
    assert name_initials("张三") == "zs"
    assert name_initials("平台教练") == "ptjl"


def test_name_search_matches_single_characters_and_initial_prefixes() -> None:
    assert name_matches_query("张三", "张")
    assert name_matches_query("张三", "三")
    assert name_matches_query("张三", "z")
    assert name_matches_query("张三", "zs")
    assert not name_matches_query("张三", "ls")
