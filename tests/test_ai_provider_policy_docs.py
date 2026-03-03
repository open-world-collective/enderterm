from pathlib import Path


def test_ai_provider_policy_is_declared_in_docs() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    readme = (repo_root / "README.md").read_text(encoding="utf-8")
    policy = (repo_root / "AI_PROVIDER_POLICY.md").read_text(encoding="utf-8")

    assert "See [AI_PROVIDER_POLICY.md](AI_PROVIDER_POLICY.md)." in readme
    assert "mass domestic surveillance" in policy
    assert "fully autonomous weaponization" in policy
    assert "Approved: Anthropic" in policy
    assert "Not approved: OpenAI" in policy
