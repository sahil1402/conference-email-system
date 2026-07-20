from app.pipeline import taxonomy as tx


def test_fourteen_intents_five_families():
    assert len(tx.VALID_INTENTS) == 14
    assert len(set(tx.VALID_INTENTS)) == 14
    assert set(tx.INTENT_FAMILIES) == set(tx.VALID_INTENTS)
    assert set(tx.INTENT_DEFS) == set(tx.VALID_INTENTS)
    assert len(tx.FAMILIES) == 5
    assert set(tx.INTENT_FAMILIES.values()) == set(tx.FAMILIES)


def test_fallback_intent_is_valid():
    assert tx.FALLBACK_INTENT in tx.VALID_INTENTS


def test_definitions_nonempty():
    assert all(tx.INTENT_DEFS[i].strip() for i in tx.VALID_INTENTS)
