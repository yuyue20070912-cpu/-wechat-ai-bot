# Streaming requests hold a thread while waiting for upstream tokens.
worker_class = 'gthread'
workers = 1
threads = 8
timeout = 180
