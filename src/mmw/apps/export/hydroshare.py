# -*- coding: utf-8 -*-
from __future__ import print_function
from __future__ import unicode_literals
from __future__ import division

import StringIO

from rauth import OAuth2Service
from urlparse import urljoin, urlparse
from hs_restclient import HydroShare, HydroShareAuthOAuth2, HydroShareNotFound

from django.conf import settings

from apps.user.models import HydroShareToken


CLIENT_ID = settings.HYDROSHARE['client_id']
CLIENT_SECRET = settings.HYDROSHARE['client_secret']
SERVICE_NAME = 'hydroshare'
BASE_URL = settings.HYDROSHARE['base_url']
HOSTNAME = urlparse(BASE_URL).hostname
AUTHORIZE_URL = urljoin(BASE_URL, settings.HYDROSHARE['authorize_url'])
ACCESS_TOKEN_URL = urljoin(BASE_URL, settings.HYDROSHARE['access_token_url'])


class HydroShareService(OAuth2Service):
    def __init__(self):
        super(HydroShareService, self).__init__(
            CLIENT_ID,
            CLIENT_SECRET,
            SERVICE_NAME,
            ACCESS_TOKEN_URL,
            AUTHORIZE_URL,
            BASE_URL
        )

    def set_token_from_code(self, code, redirect_uri, user):
        data = {'code': code, 'redirect_uri': redirect_uri,
                'grant_type': 'authorization_code'}

        res = self.get_raw_access_token(data=data).json()
        if 'error' in res:
            raise RuntimeError(res['error'])

        token, _ = HydroShareToken.objects.update_or_create(user=user,
                                                            defaults=res)

        return token

    def renew_access_token(self, user_id):
        # TODO Add checks for when user not found, refresh_token is null
        token = HydroShareToken.objects.get(user_id=user_id)
        data = {'refresh_token': token.refresh_token,
                'grant_type': 'refresh_token'}

        res = self.get_raw_access_token(data=data).json()
        if 'error' in res:
            raise RuntimeError(res['error'])

        for key, value in res.iteritems():
            setattr(token, key, value)
        token.save()

        return token

    def get_client(self, user_id):
        # TODO Add check for when user not found
        token = HydroShareToken.objects.get(user_id=user_id)
        if token.is_expired:
            token = self.renew_access_token(user_id)

        auth = HydroShareAuthOAuth2(CLIENT_ID, CLIENT_SECRET,
                                    token=token.get_oauth_dict())

        return HydroShareClient(hostname=HOSTNAME, auth=auth)


class HydroShareClient(HydroShare):
    """
    Helper class for utility methods for HydroShare
    """

    def add_files(self, resource_id, files, overwrite=False):
        """
        Helper method that will add an array of files to a resource.

        :param resource_id: ID of the resource to add files to
        :param files: List of dicts in the format
                      {'name': 'String', 'contents': 'String', 'object': False}
                      or
                      {'name': 'String', 'contents': file_like_object, 'object': True}  # NOQA
        :param overwrite: Whether to overwrite files or not. False by default.
        """

        for f in files:
            fobject = f.get('object', False)
            fcontents = f.get('contents')
            fname = f.get('name')
            if fcontents and fname:
                # Overwrite files if specified
                if overwrite:
                    try:
                        # Delete the resource file if it already exists
                        self.deleteResourceFile(resource_id, fname)
                    except HydroShareNotFound:
                        # File didn't already exists, move on
                        pass

                if fobject:
                    fio = fcontents
                else:
                    fio = StringIO.StringIO()
                    fio.write(fcontents)

                # Add the new file
                self.addResourceFile(resource_id, fio, fname)

    def check_resource_exists(self, resource_id):
        try:
            self.getSystemMetadata(resource_id)
            return True
        except HydroShareNotFound:
            return False

    def get_file_list(self, resource_id):
        try:
            return self.getResourceFileList(resource_id)
        except HydroShareNotFound:
            return None
