# -*- coding: utf-8 -*-
from __future__ import print_function
from __future__ import unicode_literals
from __future__ import division

import fiona
import json
import os
import StringIO
import tempfile
import zipfile

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.contrib.gis.geos import GEOSGeometry
from django.http import HttpResponse
from django.shortcuts import get_object_or_404

from rest_framework import decorators, status
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.modeling.models import Project
from apps.modeling.serializers import AoiSerializer
from apps.modeling.tasks import to_gms_file
from apps.geoprocessing_api.views import start_celery_job

from hydroshare import HydroShareService
from models import HydroShareResource
from serializers import HydroShareResourceSerializer
from tasks import (
    create_resource,
    add_file,
    add_metadata,
    add_shapefile,
    link_to_project_and_save,
    update_hydroshare_resource,
)

hss = HydroShareService()
HYDROSHARE_BASE_URL = settings.HYDROSHARE['base_url']
SHAPEFILE_EXTENSIONS = ['cpg', 'dbf', 'prj', 'shp', 'shx']
DEFAULT_KEYWORDS = {'mmw', 'model-my-watershed'}
MMW_APP_KEY_FLAG = '{"appkey": "model-my-watershed"}'


@decorators.api_view(['GET', 'POST', 'PATCH', 'DELETE'])
@decorators.permission_classes((IsAuthenticated, ))
def hydroshare(request):
    # Get HydroShare client with user's credentials
    try:
        hs = hss.get_client(request.user.id)
    except ObjectDoesNotExist:
        return Response(
            data={'errors': ['User not connected to HydroShare']},
            status=status.HTTP_401_UNAUTHORIZED
        )

    project_id = request.GET.get('project')
    if not project_id:
        # No support for exporting without a project right now
        return Response(
            data={'errors': ['Cannot export to HydroShare without project']},
            status=status.HTTP_400_BAD_REQUEST
        )

    params = request.data
    project = get_object_or_404(Project, id=project_id,
                                user__id=request.user.id)

    try:
        hsresource = HydroShareResource.objects.get(project=project)
    except HydroShareResource.DoesNotExist:
        hsresource = None

    # GET existing resource simply returns it
    if hsresource and request.method == 'GET':
        if not hs.check_resource_exists(hsresource.resource):
            return Response(
                data={
                    'errors': ['HydroShare could not find requested resource']
                },
                status=status.HTTP_404_NOT_FOUND
            )
        serializer = HydroShareResourceSerializer(hsresource)
        return Response(serializer.data)

    # PATCH existing resource updates its autosync status
    if hsresource and request.method == 'PATCH':
        autosync = params.get('autosync', None)
        if autosync is None:
            return Response(
                data={'errors': ['Must specify autosync as true or false']},
                status=status.HTTP_400_BAD_REQUEST
            )
        hsresource.autosync = autosync
        hsresource.save()
        serializer = HydroShareResourceSerializer(hsresource)
        return Response(serializer.data)

    # DELETE existing resource removes it from MMW and HydroShare
    if hsresource and request.method == 'DELETE':
        if hs.check_resource_exists(hsresource.resource):
            hs.deleteResource(hsresource.resource)
        hsresource.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    # Cannot GET, PATCH or DELETE non-existing resource
    if not hsresource and request.method in ['GET', 'PATCH', 'DELETE']:
        return Response(status=status.HTTP_404_NOT_FOUND)

    # POST existing resource updates it
    if hsresource and request.method == 'POST':
        # Get list of existing files to see if some can be skipped
        existing_files = hs.get_file_list(hsresource.resource)
        if not existing_files:
            return Response(
                data={
                    'errors': ['HydroShare could not find requested resource']
                },
                status=status.HTTP_404_NOT_FOUND
            )

        current_analyze_files = [
            f['url'][(1 + f['url'].index('/analyze_')):]
            for f in existing_files
            if '/analyze_' in f['url']
        ]

        # Update files
        files = params.get('files', [])
        for md in params.get('mapshed_data', []):
            mdata = md.get('data')
            files.append({
                'name': md.get('name'),
                'contents': to_gms_file(json.loads(mdata)) if mdata else None,
                'object': True
            })

        # Except the existing analyze files
        files = [f for f in files if f['name'] not in current_analyze_files]

        chain = [add_file.s(hsresource.resource,
                            request.user.id, files[0], overwrite=True)]

        for f in files[1:]:
            chain.append(add_file.s(request.user.id, f, overwrite=True))

        chain.append(update_hydroshare_resource.s(project_id))

        return start_celery_job(chain, project_id, request.user)

    # POST new resource creates it in HydroShare
    title = params.get('title', project.name)
    abstract = params.get('abstract', '')
    keywords = params.get('keywords')
    files = params.get('files', [])
    autosync = params.get('autosync', True)

    # AoI GeoJSON
    aoi_geojson = GEOSGeometry(project.area_of_interest).geojson
    aoi_json = json.loads(aoi_geojson)
    files.append({
        'name': 'area-of-interest.geojson',
        'contents': aoi_geojson,
    })

    # MapShed Data
    for md in params.get('mapshed_data', []):
        mdata = md.get('data')
        files.append({
            'name': md.get('name'),
            'contents': to_gms_file(json.loads(mdata)) if mdata else None,
            'object': True
        })

    chain = [create_resource.s(request.user.id, title, abstract, keywords)]

    for f in files:
        chain.append(add_file.s(request.user.id, f))

    chain.append(add_shapefile.s(request.user.id, aoi_json))
    chain.append(add_metadata.s(request.user.id))
    chain.append(link_to_project_and_save.s(project_id, title, autosync))

    return start_celery_job(chain, project_id, request.user)


@decorators.api_view(['POST'])
def shapefile(request):
    """Convert a GeoJSON to a Shapefile"""

    # Extract area of interest into a dictionary
    params = request.data
    aoi_json = params.get('shape', '{}')
    filename = params.get('filename', 'area-of-interest')

    # Validate Shape
    serializer = AoiSerializer(data={'area_of_interest': aoi_json})
    serializer.is_valid(raise_exception=True)
    aoi_json = json.loads(serializer.validated_data.get('area_of_interest'))

    # Configure Shapefile Settings
    crs = {'no_defs': True, 'proj': 'longlat',
           'ellps': 'WGS84', 'datum': 'WGS84'}
    schema = {'geometry': aoi_json['type'], 'properties': {}}

    # Make a temporary directory to save the files in
    tempdir = tempfile.mkdtemp()

    # Write shapefiles
    with fiona.open('{}/area-of-interest.shp'.format(tempdir), 'w',
                    driver='ESRI Shapefile',
                    crs=crs, schema=schema) as sf:
        sf.write({'geometry': aoi_json, 'properties': {}})

    shapefiles = ['{}/area-of-interest.{}'.format(tempdir, ext)
                  for ext in SHAPEFILE_EXTENSIONS]

    # Create a zip file in memory from all the shapefiles
    stream = StringIO.StringIO()
    with zipfile.ZipFile(stream, 'w') as zf:
        for fpath in shapefiles:
            _, fname = os.path.split(fpath)
            zf.write(fpath, fname)
            os.remove(fpath)

    # Delete the temporary directory
    os.rmdir(tempdir)

    # Return the zip file from memory with appropriate headers
    resp = HttpResponse(stream.getvalue(), content_type='application/zip')
    resp['Content-Disposition'] = 'attachment; '\
                                  'filename="{}.zip"'.format(filename)
    return resp
