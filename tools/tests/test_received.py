from stubs import FLOW_ARN, StubClient, source_metadata

from livectl.received import received

NOW = 1_790_000_000_000


def read(answer):
    return received(StubClient(describe_flow_source_metadata=answer), FLOW_ARN, clock_ms=lambda: NOW)


def test_a_full_answer_is_normalised():
    result = read(source_metadata(name="Match 1", fps="25"))

    program = result["programs"][0]
    assert (program["name"], program["number"]) == ("Match 1", 1)
    video, audio = program["streams"]
    assert video == {"type": "video", "codec": "h264", "width": 1920, "height": 1080, "fps": 25.0,
                     "channels": None, "sample_rate": None}
    assert (audio["type"], audio["codec"], audio["channels"], audio["sample_rate"]) == ("audio", "aac", 2, 48000)
    assert result["messages"] == [] and result["at"] == NOW


def test_nothing_parsed_yet():
    answer = {"FlowArn": FLOW_ARN, "Messages": [], "TransportMediaInfo": {"Programs": []}}

    assert read(answer) == {"programs": [], "messages": [], "at": NOW}


def test_messages_only():
    answer = {"FlowArn": FLOW_ARN, "Messages": [{"Code": "NO_SOURCE", "Message": "No source", "ResourceName": "s"}]}

    assert read(answer) == {"programs": [], "messages": ["No source"], "at": NOW}


def test_missing_fields_become_none():
    answer = {"TransportMediaInfo": {"Programs": [{"Streams": [{"StreamType": "Video", "Codec": "h264"}]}]}}

    program = read(answer)["programs"][0]
    assert (program["name"], program["number"]) == (None, None)
    stream = program["streams"][0]
    assert (stream["width"], stream["height"], stream["fps"]) == (None, None, None)


def test_fractional_frame_rate():
    assert read(source_metadata(fps="30000/1001"))["programs"][0]["streams"][0]["fps"] == 29.97


def test_an_unreadable_frame_rate_becomes_none():
    assert read(source_metadata(fps="n/a"))["programs"][0]["streams"][0]["fps"] is None
