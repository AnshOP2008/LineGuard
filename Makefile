.PHONY: install data evaluate evaluate-deep export serve test demo
install:        ; pip install -r requirements-dev.txt
data:           ; python -m lineguard download
evaluate:       ; python -m lineguard evaluate
evaluate-deep:  ; python -m lineguard evaluate --deep
export:         ; python -m lineguard export
serve:          ; python -m lineguard serve
test:           ; python -m pytest -q
demo: data evaluate export serve
