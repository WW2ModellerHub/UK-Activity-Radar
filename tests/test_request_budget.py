from app.request_budget import FixedWindowBudget


def test_budget_enforces_per_client_limit_and_resets():
    now = [100.0]
    budget = FixedWindowBudget(
        per_client=2,
        global_limit=10,
        window_seconds=60,
        clock=lambda: now[0],
    )

    assert budget.consume("alpha")[0] is True
    assert budget.consume("alpha")[0] is True
    allowed, retry_after = budget.consume("alpha")
    assert allowed is False
    assert retry_after == 60
    assert budget.consume("bravo")[0] is True

    now[0] += 61
    assert budget.consume("alpha")[0] is True


def test_budget_enforces_global_limit_across_clients():
    budget = FixedWindowBudget(per_client=2, global_limit=3, clock=lambda: 100.0)

    assert budget.consume("alpha")[0] is True
    assert budget.consume("bravo")[0] is True
    assert budget.consume("charlie")[0] is True
    assert budget.consume("delta")[0] is False
    assert budget.status() == {
        "limit": 3,
        "used": 3,
        "remaining": 0,
        "rejected": 1,
        "seconds_remaining": 60,
    }


def test_budget_status_resets_without_exposing_client_keys():
    now = [100.0]
    budget = FixedWindowBudget(per_client=1, global_limit=2, clock=lambda: now[0])
    budget.consume("private-client-address")
    budget.consume("private-client-address")

    assert "private-client-address" not in str(budget.status())
    now[0] += 61
    assert budget.status() == {
        "limit": 2,
        "used": 0,
        "remaining": 2,
        "rejected": 0,
        "seconds_remaining": 60,
    }
