# Users, groups and permissions (roadmap 3.0): agreed design

Settled with the owner on 2026-11-14. Builds on the central check in `backend/app/permissions.py` (2.15), which already
decides every "may this person do this" question in one place, so the rework changes that module and the data behind it,
not every route.

## The model

- **Groups.** A group is a society, course, lab or team. Admins create groups. There is always a **default group** (for
  example "ICRS"). New users join it automatically, with the default group's default role.
- **Membership.** A person can belong to several groups, with **one role in each group**. Their rights for anything that
  belongs to a group (its prints, its printers, its members) come from their role in that group.
- **Roles are custom.** Admins define roles as a named set of rights (ticked from a fixed list of rights). Three roles are
  built in:
  - **Admin**: fixed. It has every right everywhere and cannot be edited. There must always be at least one active admin.
  - **Operator**: editable. It starts with today's operator rights plus managing the group's members. The owner merged
    "group lead" into operator, so each group's operators have escalated rights and manage their group (invite and remove
    members, change their roles within the group, approve, line jobs up).
  - **Member**: editable. It starts with today's member rights.
- **Prints belong to a group.** When submitting or printing, a person picks which of their groups the print is for. The
  default is their main group. Quotas (3.2), approvals (3.3) and costs (3.4) will count against that group.
- **Printers are shared by default.** An admin can limit a printer to chosen groups (for example a lab's own printer).
  Rights over shared printers (operating them, clearing plates, maintenance) come from the person's role in the default
  group. Rights over a restricted printer come from their role in one of the groups it is limited to.
- **Choosing a printer.** This is a right that a role can include ("choose a specific printer when submitting"). A group
  whose role grants it lets those members pick a printer. Nobody else can, and staff can still line jobs up on the Queue page.
- **Offboarding.** Disabling a person stops their sign-in at once. Their jobs, parts and history are kept for statistics and
  can be handed to a group operator.
- **Migration.** Existing admins become Admin. Users with the operator flag become Operator in the default group. Everyone
  else becomes Member in the default group. Nothing anyone can do today changes on the day of the migration.

## Not decided yet

- How Imperial SSO (Entra) groups map to groups and roles. SSO is on the back burner (0.9), so this waits for it.
- Quotas, approvals and costs per group are 3.2, 3.3 and 3.4. They only need "a print belongs to a group" from here.

## Rights list (first draft, to confirm while building)

These are today's actions from `permissions.py`, reorganised as rights a role can tick:

- see all jobs of the group
- manage jobs of the group (cancel, stop)
- pause and resume prints
- clear plates, mark plates occupied
- printer maintenance and mark ready
- manage the queue (reorder, line up)
- manage filament
- see failure patterns
- see who is using printers
- manage group members (invite, remove, change role within the group)
- choose a specific printer when submitting
- share presets with the group

Admin-only and not delegable: users across all groups, roles, groups, printer set-up, gateways, notifications channels,
storage, pipeline settings, audit, dashboard.
