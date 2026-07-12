from skillrace.parallel_campaign import candidate_id, make_reservations


def test_candidate_id_is_stable_and_scope_sensitive():
    first = candidate_id("protocol/rep-001/random/demo", "e0004", "e0004-a01", 0)

    assert first == candidate_id(
        "protocol/rep-001/random/demo", "e0004", "e0004-a01", 0
    )
    assert first != candidate_id(
        "protocol/rep-001/random/demo", "e0004", "e0004-a01", 1
    )
    assert first != candidate_id(
        "protocol/rep-001/greybox/demo", "e0004", "e0004-a01", 0
    )
    assert first.startswith("cand-") and len(first) == 21


def test_candidate_id_rejects_ambiguous_or_out_of_range_components():
    for arguments in (
        ("", "e0000", "e0000-a00", 0),
        ("campaign", "", "e0000-a00", 0),
        ("campaign", "e0000", "", 0),
        ("campaign", "e0000", "e0000-a00", -1),
        ("campaign", "e0000", "e0000-a00", True),
    ):
        try:
            candidate_id(*arguments)
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid identity components accepted: {arguments!r}")


def test_reservations_bind_epoch_coordinates_and_slot_to_candidate_identity():
    reservations = make_reservations(
        "protocol/rep-001/random/demo",
        [("e0004", "e0004-a00"), ("e0005", "e0005-a00")],
        epoch=2,
    )

    assert [item.slot for item in reservations] == [0, 1]
    assert [item.epoch for item in reservations] == [2, 2]
    assert reservations[0].candidate_id == candidate_id(
        "protocol/rep-001/random/demo", "e0004", "e0004-a00", 0
    )
    assert reservations[1].provenance == {
        "campaign_id": "protocol/rep-001/random/demo",
        "execution_id": "e0005",
        "attempt_id": "e0005-a00",
        "epoch": 2,
        "slot": 1,
    }


def test_reservations_reject_duplicate_execution_attempt_coordinates():
    try:
        make_reservations(
            "protocol/rep/random/demo",
            [("e0004", "e0004-a00"), ("e0004", "e0004-a00")],
            epoch=2,
        )
    except ValueError as error:
        assert "duplicate" in str(error)
    else:
        raise AssertionError("duplicate reservation coordinates were accepted")
