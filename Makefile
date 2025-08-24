.PHONY: install setup-log watch suggest ui demo feed check test test-replay test-tail

PY ?= python3
FEED_INTERVAL ?= 25
PICKS ?= 12

install:
	$(PY) -m pip install -r requirements.txt

setup-log:
	./tools/setup_player_log_symlink.sh

watch:
	./runlog.sh

suggest:
	./runlog.sh suggest

ui:
	$(PY) frontend/ui_server.py

demo:
	UI_DATA_DIR=frontend/fixtures/demo_1 $(PY) frontend/ui_server.py

feed:
	$(PY) tools/mock_draft_feed.py --interval $(FEED_INTERVAL) --picks $(PICKS)

check:
	$(PY) tools/check_ui_consistency.py

test:
	pytest -q

test-replay:
	pytest -q tests/test_watch_replay_mock_feed.py -k replay

test-tail:
	RUN_TAIL=1 pytest -q tests/test_watch_replay_mock_feed.py -k tail

