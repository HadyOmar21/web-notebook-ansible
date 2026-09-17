# Notes App — Ansible-Deployed Note-Taking Website on AWS EC2

## Objective

Deploy a simple Python note-taking web application to an AWS EC2 instance (Amazon Linux) using a custom Ansible role, with SQLite as storage and a scheduled backup strategy to a second EC2 instance.

## Architecture

```
                     ┌─────────────────────────┐
                     │   Ansible Controller     │
                     │  (runs playbook.yml,     │
                     │   holds ansible.pem)     │
                     └────────────┬─────────────┘
                                   │ SSH
                 ┌─────────────────┴─────────────────┐
                 ▼                                    ▼
     ┌───────────────────────┐          ┌───────────────────────┐
     │   web  (EC2 instance) │          │   db  (EC2 instance)  │
     │                       │          │                       │
     │  nginx  :80 (public)  │          │  /home/ec2-user/      │
     │     │ reverse proxy   │  backup  │     backups/          │
     │     ▼                 │ ───────▶ │  (timestamped .db     │
     │  Python app :5000     │  (fetch  │   copies)             │
     │  (127.0.0.1 only)     │  + copy) │                       │
     │     │                 │          │                       │
     │     ▼                 │          │                       │
     │  notes.db (SQLite)    │          │                       │
     └───────────────────────┘          └───────────────────────┘
```

- **nginx** listens on port 80 (public) and reverse-proxies to the Python app.
- The **Python app** (standard library only — `http.server` + `sqlite3`, no pip installs) binds to `127.0.0.1:5000` only — never directly reachable from the internet.
- **SQLite** is a single file (`notes.db`) on the `web` host's disk — no separate database service or instance.
- **Backups**: Ansible fetches `notes.db` from `web` down to the controller, then copies it up to `db` as a timestamped file. Scheduled via cron on the controller.

## Prerequisites

- AWS Free Tier account
- Two EC2 instances (Amazon Linux, t2.micro): one tagged `web`, one tagged `db`
- Security Group on `web` allowing inbound TCP 22 (SSH) and 80 (HTTP)
- A `.pem` key pair for SSH access to both instances
- Ansible installed on a controller (can be a third small instance or your local machine)
- `ansible.posix` collection: `ansible-galaxy collection install ansible.posix`

## Project Structure

```
notes-app-ansible/
├── ansible.cfg
├── ansible.pem                  # SSH private key (gitignored — never commit)
├── playbook.yml
├── inventory/
│   ├── production               # [web] and [db] host groups
│   └── group_vars/
│       └── all.yml              # app_name, app_user, app_dir, app_port, db_path
└── roles/
    ├── notes-app/
    │   ├── tasks/
    │   │   ├── main.yml         # imports the four files below, in order
    │   │   ├── prerequisites.yml# yum update, python3, sqlite, app user/dir
    │   │   ├── backend.yml      # deploy app.py, systemd service
    │   │   ├── frontend.yml     # stop Apache, install/configure nginx
    │   │   └── security.yml     # firewalld rules, file permission hardening
    │   ├── handlers/main.yml    # restart notes-backend, restart nginx
    │   ├── files/app.py         # the Flask-free Python app (static, not templated)
    │   └── templates/
    │       ├── notes-backend.service.j2
    │       ├── nginx.conf.j2
    │       └── notes-app.conf.j2
    └── backup/
        └── tasks/main.yml       # fetch from web, copy to db
```

## Setup & Deployment

1. Update `inventory/production` with your real EC2 public IPs under `[web]` and `[db]`.
2. Update `inventory/group_vars/all.yml` if you want different app settings.
3. Dry-run first: `ansible-playbook playbook.yml --check`
4. Deploy: `ansible-playbook playbook.yml`
5. Run backup only: `ansible-playbook playbook.yml --tags backup`

## Testing

- App: open `http://<web-instance-public-ip>/` in a browser, submit a note, confirm it appears at the top with a timestamp.
- Backend directly: `curl -s http://localhost/` on the `web` instance.
- Service status: `sudo systemctl status notes-backend nginx` on `web`.
- Backup: `ls -la /home/ec2-user/backups/` on the `db` instance — should contain timestamped `.db` files.
- Backup contents: `sqlite3 <backup-file>.db "SELECT * FROM notes;"`

## Backup Strategy

A separate Ansible play, tagged `backup`, delegates a `fetch` task to each `web` host (pulling `notes.db` to the controller), then `copy`s it up to the `db` host under `/home/ec2-user/backups/` with a timestamped filename. This is scheduled hourly via a controller-side cron job:

```
0 * * * * cd /home/ec2-user/notes-app-ansible && /usr/bin/ansible-playbook playbook.yml --tags backup >> /var/log/notes-backup.log 2>&1
```

## Security Notes

- App runs as a dedicated, unprivileged system user (`notesapp`), not root or `ec2-user`.
- App only binds to `127.0.0.1` — nginx is the only public-facing process.
- `firewalld` runs alongside the AWS Security Group as a second, host-level defense layer.
- `notes.db` and the app directory are restricted (`0750`/`0640`) to the app user's group only.
- `ansible.pem` and any `.pem`/`*.db` backup files should never be committed to version control.

## Troubleshooting Log (issues hit during this build)

These were real failures encountered deploying this project — kept here since they're likely to recur on a fresh Amazon Linux AMI:

| Symptom | Cause | Fix |
|---|---|---|
| `expected token 'end of print statement', got ':'` when deploying `app.py` | `app.py` was a Jinja2 `.j2` template, but its embedded CSS used `{{ }}` for f-string brace-escaping — Jinja tried to parse the CSS as a variable expression | Stopped templating `app.py` itself; made it a static file deployed with `copy`, and passed `app_port`/`db_path` into it via environment variables set in the systemd unit template instead |
| `nginx: [emerg] no "events" section in configuration` | `/etc/nginx/nginx.conf` was 0 bytes on this AMI (vendor default was never populated) | Added an explicit `nginx.conf.j2` template deployed by the role, instead of relying on the package's default file existing |
| `nginx: [emerg] bind() to 0.0.0.0:80 failed (Address already in use)` | Apache (`httpd`) was pre-installed and running on the AMI, already holding port 80 | Added a task to stop and disable `httpd` before installing/starting nginx |
| Backup `copy` task: `Could not find or access '/tmp/notes-backups/<ip>-notes.db' on the Ansible Controller` | `fetch` task used `{{ inventory_hostname }}` in its `dest` filename under `delegate_to` — but `inventory_hostname` still refers to the *play's* host (`db`), not the delegated-to host, so the actual saved filename didn't match what the next task looked for | Used the loop variable `{{ item }}` consistently in both the `fetch` and `copy` tasks, since it correctly tracks the delegated host regardless of delegation |
| Emoji (🕒 📌 📝) rendering as garbled text (`ðŸ"`) in the browser | `Content-type` header didn't declare `charset=utf-8`, so the browser guessed the wrong encoding for the UTF-8 bytes being sent | Set `Content-type: text/html; charset=utf-8` explicitly in the response header |
| `ansible-playbook playbook.yml --tags backup` ran but did nothing | Used a tag that was never defined (`db`, an inventory group name, not a tag) and `playbook.yml` didn't yet have `tags: backup` set on the second play | Added `tags: backup` to the backup play in `playbook.yml` |

## Deliverables Checklist

- [x] Custom Ansible role (`notes-app`) with `tasks/`, `handlers/`, `templates/`, `files/`
- [x] EC2 deployment via `ansible-playbook`
- [x] Python note-taking app (standard library only, no external framework)
- [x] SQLite storage with timestamped notes
- [x] Backup strategy to a second EC2 instance, scheduled via cron
- [x] This README
