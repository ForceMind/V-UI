# ToClash-derived components

Source: ForceMind/ToClash, commit 95a5c71a516c10f97f47bfb771018ce890b2b570, version 0.3.8.
Source repository: https://github.com/ForceMind/ToClash
License: Apache License 2.0; full unmodified license is retained as ToClash-LICENSE.

V-UI's app/services/mihomo_routing.py is a modified Python port of ToClash's rules/plan.ts, rules/defaults.ts, rules/presets.ts and policy-group/name allocation behavior. app/services/routing_validation.py supplies a stricter validated boundary and corrects URL/IP normalization differences in the initial port. The data, algorithms and attribution are not represented as unrelated original work.

The pinned source contains no root NOTICE file. CI copies any source NOTICE alongside its reference artifacts if one is present; changing the pin requires a new attribution/license review.

The independent reference runs only in development/CI. No ToClash conversion service, Node daemon or external transfer of node credentials is used by the running panel.
