"""Immich uploads and desktop-keyring storage. No credentials are written to settings."""
from datetime import datetime, timezone, timedelta
import hashlib
import re
import secrets
import string
from urllib.parse import urlsplit, urlunsplit, quote
import requests
import gi
gi.require_version('Secret', '1')
from gi.repository import Secret

SCHEMA = Secret.Schema.new('io.github.isaac.Snaplight.Immich', Secret.SchemaFlags.NONE,
                           {'server': Secret.SchemaAttributeType.STRING})


class UploadError(Exception):
    pass


def server_url(value):
    value = value.strip().rstrip('/')
    if not value: raise ValueError('Enter your Immich server URL in Settings.')
    if '://' not in value: value = 'https://'+value
    p = urlsplit(value)
    if p.scheme not in ('https', 'http') or not p.hostname or p.username or p.password or p.query or p.fragment:
        raise ValueError('Enter a server URL such as https://photos.example.com.')
    path = p.path.rstrip('/')
    if path.endswith('/api'): path = path[:-4]
    return urlunsplit((p.scheme, p.netloc, path, '', ''))


def store_key(server, key):
    if not key.strip(): return
    if not Secret.password_store_sync(SCHEMA, {'server': server_url(server)}, Secret.COLLECTION_DEFAULT,
                                      'Goatshot — Immich API key', key.strip(), None):
        raise UploadError('The desktop keyring could not save the API key.')


def lookup_key(server):
    key = Secret.password_lookup_sync(SCHEMA, {'server': server_url(server)}, None)
    if not key: raise UploadError('Enter an Immich API key in Goatshot Settings first.')
    return key


def api_request(session, method, url, key, **kwargs):
    try:
        response = session.request(method, url, headers={'x-api-key': key}, timeout=(10, 90),
                                   allow_redirects=False, **kwargs)
    except requests.RequestException:
        # Do not include request objects, headers, or credentials in errors.
        raise UploadError('Could not reach Immich. Check the server address and your connection.') from None
    if response.status_code in (401, 403):
        if method in ('PUT', 'PATCH') and '/api/assets/' in url:
            raise UploadError('Immich could not archive the existing screenshot. The API key needs asset.update permission.')
        raise UploadError('Immich rejected the API key. It needs asset.upload and sharedLink.create permissions.')
    if 300 <= response.status_code < 400:
        raise UploadError('Immich redirected the request. Use the final server URL in Settings.')
    return response


def short_slug():
    return ''.join(secrets.choice(string.ascii_lowercase+string.digits) for _ in range(6))


def validate_slug(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', value):
        raise ValueError('Use 1–64 letters, numbers, hyphens, or underscores for the vanity URL.')
    return value


def slug_conflict(response):
    if response.status_code == 409: return True
    if response.status_code != 400: return False
    try: message = str(response.json().get('message', '')).lower()
    except (ValueError, AttributeError): return False
    return any(phrase in message for phrase in ('failed to save shared link', 'already exists', 'duplicate slug', 'slug is taken'))


def upload_and_share(png, server, key, expiry_days=0, session=None, custom_slug=None):
    base = server_url(server)
    if custom_slug is not None: validate_slug(custom_slug)
    now = datetime.now(timezone.utc)
    stamp = now.isoformat().replace('+00:00', 'Z')
    digest = hashlib.sha256(png).hexdigest()
    filename = 'Screenshot-'+now.strftime('%Y-%m-%d-%H%M%S')+'.png'
    fields = {'fileCreatedAt': stamp, 'fileModifiedAt': stamp, 'visibility': 'archive'}
    owned = session is None
    session = session or requests.Session()
    try:
        response = api_request(session, 'POST', base+'/api/assets', key,
                               data=fields, files={'assetData': (filename, png, 'image/png')})
        # Older Immich releases require device identifiers; current versions do not.
        if response.status_code == 400 and any(name in response.text for name in ('deviceAssetId', 'deviceId')):
            fields.update(deviceAssetId='goatshot-'+digest, deviceId='goatshot-desktop')
            response = api_request(session, 'POST', base+'/api/assets', key,
                                   data=fields, files={'assetData': (filename, png, 'image/png')})
        if response.status_code not in (200, 201):
            raise UploadError(f'Immich could not upload the screenshot (HTTP {response.status_code}).')
        try:
            upload_result = response.json()
            asset_id = upload_result['id']
            if not isinstance(asset_id, str) or not asset_id: raise ValueError()
        except (ValueError, KeyError, TypeError):
            raise UploadError('Immich returned an unexpected upload response.') from None
        # Duplicate uploads reuse an existing asset; creation fields do not update it.
        if upload_result.get('status') == 'duplicate' or response.status_code == 200:
            try:
                archived = api_request(session, 'PUT', base+'/api/assets/'+quote(asset_id, safe=''),
                                       key, json={'visibility': 'archive'})
                if archived.status_code not in (200, 204):
                    raise UploadError(f'Could not archive the screenshot (HTTP {archived.status_code}).')
            except UploadError as e:
                raise UploadError('The screenshot is already in Immich, but archiving failed. '+str(e)) from None
        body = {'type': 'INDIVIDUAL', 'assetIds': [asset_id], 'allowDownload': True,
                'allowUpload': False, 'showMetadata': False}
        if expiry_days:
            body['expiresAt'] = (now+timedelta(days=expiry_days)).isoformat().replace('+00:00', 'Z')
        try:
            for attempt in range(5):
                slug = custom_slug if custom_slug is not None else short_slug()
                response = api_request(session, 'POST', base+'/api/shared-links', key,
                                       json={**body, 'slug': slug})
                if slug_conflict(response):
                    if custom_slug is not None:
                        raise UploadError('That vanity URL is already taken. Choose another name.')
                    if attempt < 4: continue
                    raise UploadError('Could not find an unused short URL. Please try again.')
                if response.status_code not in (200, 201):
                    raise UploadError(f'Could not create a share link (HTTP {response.status_code}).')
                result = response.json()
                if result.get('slug') != slug:
                    raise UploadError('Immich did not accept the short URL. Check that your server supports custom share URLs.')
                return base+'/s/'+quote(slug, safe='')
        except (UploadError, ValueError, TypeError, AttributeError) as e:
            message = str(e) if isinstance(e, UploadError) else 'Immich returned an unexpected sharing response.'
            raise UploadError('The screenshot is in Immich, but sharing failed. '+message) from None
    finally:
        if owned: session.close()
