# Device integration foundation

`backend/integrations/device.py` defines provider and connection contracts for future device status, notifications, file transfer, approved app interactions, and smart-home integrations. No Android or device provider is implemented, no pairing can be initiated, and no device action is advertised as available. `/api/devices/status` reports this accurately.

Future providers must add a local explicit pairing and revocation flow, bind each device to approved capabilities, route actions through `ToolRegistry` and `PermissionPolicy`, and audit every operation. Nothing connects automatically.
