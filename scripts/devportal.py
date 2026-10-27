"""A small Xbox Device Portal client (https://<console>:11443): app packages, an app's LocalState files,
launching and stopping apps, screenshots. Standard library only.

Device Portal on a Dev Mode console uses a self-signed certificate, so TLS is not verified. With "Require
authentication" on, pass the user name and password set in Dev Home (basic authentication)."""
import base64
import http.client
import json
import os
import ssl
import time
import urllib.parse
import uuid


class DevicePortal:
    def __init__(self, host, user=None, password=None, port=11443):
        self.host, self.port = host, port
        self.ctx = ssl._create_unverified_context()
        self.cookies = {}
        self.auth = None
        if user:
            self.auth = 'Basic ' + base64.b64encode(f'{user}:{password or ""}'.encode()).decode()

    # ---- HTTP
    def request(self, method, path, body=None, headers=None, tries=4, timeout=600):
        for attempt in range(tries):
            try:
                c = http.client.HTTPSConnection(self.host, self.port, context=self.ctx, timeout=timeout)
                h = dict(headers or {})
                if self.auth:
                    h['Authorization'] = self.auth
                if self.cookies:
                    h['Cookie'] = '; '.join(f'{k}={v}' for k, v in self.cookies.items())
                    if 'CSRF-Token' in self.cookies:
                        h['X-CSRF-Token'] = urllib.parse.unquote(self.cookies['CSRF-Token'])
                if method in ('POST', 'DELETE') and body is None:
                    h['Content-Length'] = '0'
                c.request(method, path, body=body, headers=h)
                r = c.getresponse()
                data = r.read()
                for k, v in r.getheaders():
                    if k.lower() == 'set-cookie':
                        name, _, rest = v.partition('=')
                        self.cookies[name.strip()] = rest.split(';', 1)[0]
                return r.status, data
            except (OSError, http.client.HTTPException) as e:
                if attempt + 1 == tries:
                    raise SystemExit(f'{method} {path}: {e} (is Device Portal on, at https://{self.host}:{self.port}?)')
                time.sleep(2 ** (attempt + 1))

    def connect(self):
        s, d = self.request('GET', '/api/os/info')  # also sets the CSRF cookie
        if s == 401:
            raise SystemExit('Device Portal asks for a login: pass --user and --password (set in Dev Home)')
        if s != 200:
            raise SystemExit(f'Device Portal answered {s}')
        return json.loads(d)

    @staticmethod
    def multipart(files):
        """files: [(field name, file name, bytes)] -> (body, content type)"""
        boundary = uuid.uuid4().hex
        parts = []
        for field, name, data in files:
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"; filename="{name}"\r\n'
                         f'Content-Type: application/octet-stream\r\n\r\n'.encode())
            parts.append(data)
            parts.append(b'\r\n')
        parts.append(f'--{boundary}--\r\n'.encode())
        return b''.join(parts), f'multipart/form-data; boundary={boundary}'

    # ---- packages
    def packages(self):
        s, d = self.request('GET', '/api/app/packagemanager/packages')
        return json.loads(d).get('InstalledPackages', []) if s == 200 else []

    def find_package(self, name):
        return next((p for p in self.packages() if p.get('PackageFullName', '').startswith(name + '_')), None)

    def install(self, package_path, extra_paths=()):
        """Installs an .msix/.appx with its dependency packages and certificate; waits for the result."""
        files = []
        for p in [package_path, *extra_paths]:
            with open(p, 'rb') as f:
                files.append((os.path.basename(p), os.path.basename(p), f.read()))
        body, ctype = self.multipart(files)
        q = urllib.parse.urlencode({'package': os.path.basename(package_path)})
        s, d = self.request('POST', '/api/app/packagemanager/package?' + q, body=body, headers={'Content-Type': ctype},
                            timeout=1800)
        if s not in (200, 202):
            raise SystemExit(f'install upload: {s} {d[:300]}')
        for _ in range(240):
            time.sleep(5)
            s, d = self.request('GET', '/api/app/packagemanager/state')
            if s == 204:
                continue  # still installing
            result = json.loads(d) if d else {}
            if s == 200 and result.get('Success', True):
                return result
            raise SystemExit(f'install failed: {s} {result or d[:300]}')
        raise SystemExit('install did not finish in 20 minutes')

    # ---- apps
    def launch(self, package):
        q = urllib.parse.urlencode({'appid': base64.b64encode(package['PackageRelativeId'].encode()).decode(),
                                    'package': base64.b64encode(package['PackageFullName'].encode()).decode()})
        s, d = self.request('POST', '/api/taskmanager/app?' + q)
        if s not in (200, 204):
            raise SystemExit(f'launch: {s} {d[:200]}')

    def stop(self, package):
        q = urllib.parse.urlencode({'package': base64.b64encode(package['PackageFullName'].encode()).decode()})
        self.request('DELETE', '/api/taskmanager/app?' + q)

    def screenshot(self):
        s, d = self.request('GET', '/ext/screenshot?download=true')
        return d if s == 200 else None

    # ---- an app's LocalAppData files
    def _q(self, package_full, remote, **extra):
        return urllib.parse.urlencode({'knownfolderid': 'LocalAppData', 'packagefullname': package_full, 'path': remote, **extra})

    def listing(self, package_full, remote):
        s, d = self.request('GET', '/api/filesystem/apps/files?' + self._q(package_full, remote))
        return {i['Name']: i for i in json.loads(d).get('Items', [])} if s == 200 else None

    def mkdir(self, package_full, parent, name):
        s, d = self.request('POST', '/api/filesystem/apps/folder?' + self._q(package_full, parent, newfoldername=name))
        if s not in (200, 204):
            raise SystemExit(f'mkdir {parent}\\{name}: {s} {d[:200]}')

    def ensure_dir(self, package_full, remote):
        """Creates each missing folder of a path like \\LocalState\\Games\\Factorio (a new app has none of them)."""
        parts = [p for p in remote.split('\\') if p]
        for i in range(1, len(parts)):
            parent = '\\' + '\\'.join(parts[:i])
            if parts[i] not in (self.listing(package_full, parent) or {}):
                self.mkdir(package_full, parent, parts[i])

    def put_files(self, package_full, remote, paths):
        files = []
        for p in paths:
            with open(p, 'rb') as f:
                files.append((os.path.basename(p), os.path.basename(p), f.read()))
        body, ctype = self.multipart(files)
        s, d = self.request('POST', '/api/filesystem/apps/file?' + self._q(package_full, remote), body=body,
                            headers={'Content-Type': ctype})
        if s not in (200, 204):
            raise SystemExit(f'upload to {remote}: {s} {d[:300]}')

    def get_file(self, package_full, remote_dir, name):
        s, d = self.request('GET', '/api/filesystem/apps/file?' + self._q(package_full, remote_dir, filename=name))
        return d if s == 200 else None

    def upload_tree(self, package_full, local, remote, log=print):
        """Uploads a folder tree under the app's LocalAppData path (e.g. \\LocalState\\Games\\Factorio), in
        batches; files already there with the same size are skipped, so a rerun resumes."""
        root = os.path.abspath(local)
        start = time.time()
        sent_bytes = sent_files = skipped = 0
        self.ensure_dir(package_full, remote)
        for here, dirs, files in os.walk(root):
            rel = os.path.relpath(here, root)
            dirs.sort()
            target = remote if rel == '.' else remote + '\\' + rel.replace(os.sep, '\\')
            have = self.listing(package_full, target) or {}
            for d in dirs:
                if d not in have:
                    self.mkdir(package_full, target, d)
            todo = []
            for f in sorted(files):
                p = os.path.join(here, f)
                size = os.path.getsize(p)
                if f in have and have[f].get('FileSize') == size:
                    skipped += 1
                    continue
                todo.append((p, size))
            batch, batch_bytes = [], 0
            for p, size in todo + [(None, 0)]:
                if p is None or (batch and (len(batch) >= 200 or batch_bytes + size > 48 * 2**20)):
                    if batch:
                        self.put_files(package_full, target, batch)
                        sent_files += len(batch)
                        sent_bytes += batch_bytes
                        el = max(time.time() - start, 1)
                        log(f'  {sent_files} files, {sent_bytes / 2**20:.0f} MB, {sent_bytes / 2**20 / el:.1f} MB/s  {target}')
                    batch, batch_bytes = [], 0
                if p is not None:
                    batch.append(p)
                    batch_bytes += size
        return sent_files, sent_bytes, skipped, time.time() - start
