"""Application services: use cases that combine domain rules with persistence.

Lock ordering (to avoid deadlocks between concurrent requests):
    order row  ->  station rows (all, by id)  ->  inventory row
Dispatch locks stations first but takes queued orders with SKIP LOCKED, so it
never waits on an order another request is already holding.
"""
