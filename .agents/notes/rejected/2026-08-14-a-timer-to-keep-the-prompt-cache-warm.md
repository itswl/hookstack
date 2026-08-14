---
title: Do not keep the prompt cache warm on a timer
status: rejected
date: 2026-08-14
scope: hookprobe
---

## Decision

No keepalive. Nothing calls the model on a schedule to hold the ~29k prefix in
the provider's cache.

## Why

It is the obvious move once you know the shape of the spend — investigations pay
a full prefix whenever they arrive too long after the previous one, and only 14
of 38 first calls hit anything — so it is worth writing down why the arithmetic
does not support it.

A keepalive call pays the same prefix as any other call. The first one is a
miss at roughly $0.13, and each subsequent one hits at roughly $0.015. Holding
the cache open therefore costs about $0.015 per interval:

- one call every 10 minutes = 6/hour = **$0.09/hour ≈ $2.16/day**
- what it saves = ~$0.13 per investigation that would otherwise have missed

That breaks even at about **17 investigations a day**. This deployment has
recorded 38 turns over several days. The keepalive would cost roughly an order
of magnitude more than the misses it prevents, and it would spend that money
continuously, including on the days nothing happens.

It is also the wrong shape for the service: an investigator that calls a model
when no alert has arrived is exactly what the budget breaker exists to prevent,
and a mechanism that quietly spends without an alert is one more thing to reason
about during an incident.

## Consequences

- Cold-start misses stay. They are visible now as the `cache` figure on the
  header chip, so if the traffic profile ever changes — an hourly patrol schedule
  busy enough to cross the break-even — the number to re-check is already there,
  along with the arithmetic above to re-run.
- The measurement this came out of, and what to do instead, is in
  [the prefix note](../implemented/2026-08-14-the-prompt-prefix-is-not-ours-to-shrink.md).
