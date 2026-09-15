---
name: django-async-pipeline
description: Django REST framework service layer, non-blocking threading/task managers, and Docker NVIDIA runtime setup.
---

# Backend & Infra Engineering Rules

1. **Non-Blocking Background Tasks:**
   - Never run long-running downloads or FFmpeg jobs directly in Django view request threads.
   - Use a lightweight thread pool (`concurrent.futures.ThreadPoolExecutor`) or Celery task runner.
   - Generate unique UUID `task_id` for tracking job state in memory/cache/db.

2. **Docker NVIDIA GPU Passthrough:**
   - In `docker-compose.yml`, always specify:
     ```yaml
     deploy:
       resources:
         reservations:
           devices:
             - driver: nvidia
               count: all
               capabilities: [gpu]
     ```

3. **File System & Resource Cleanup:**
   - Manage temporary fragments in `/app/media/temp/` and final outputs in `/app/media/renders/`.
   - Implement automated cleanup for temporary partial clip downloads after final render compilation.