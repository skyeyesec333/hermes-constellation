from constellation.budgeting import build_budget_plan
from constellation.cli import main


def test_preflight_cli_returns_budget_without_ingesting_source(tmp_path, capsys):
    vault = tmp_path / "vault"
    source = tmp_path / "card.txt"
    source.write_text("Fictional card", encoding="utf-8")

    assert main(["init", str(vault)]) == 0
    capsys.readouterr()
    assert main(["preflight", str(vault), str(source), "--task", "business_card"]) == 0
    result = __import__("json").loads(capsys.readouterr().out)["result"]

    assert result["task_kind"] == "business_card"
    assert result["phases"][0]["model_tokens"] == 0
    assert not (vault / "Sources").exists()


def test_large_book_preflight_requires_explicit_longform_mode_and_reserves_synthesis():
    plan = build_budget_plan(
        task_kind="book",
        source_bytes=3_000_000,
        estimated_pages=700,
    )

    assert plan.task_kind == "book"
    assert plan.requires_confirmation is True
    assert "explicit long-form mode" in plan.warnings
    assert plan.phases[0]["name"] == "extract_and_map"
    assert plan.phases[0]["model_tokens"] == 0
    assert plan.synthesis_reserve > 0
