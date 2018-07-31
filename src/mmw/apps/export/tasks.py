# -*- coding: utf-8 -*-
from __future__ import print_function
from __future__ import unicode_literals
from __future__ import division

import fiona
import json
import os

from celery import shared_task

from django.utils.timezone import now

from apps.modeling.models import Project

from hydroshare import HydroShareService
from models import HydroShareResource
from serializers import HydroShareResourceSerializer

hss = HydroShareService()

SHAPEFILE_EXTENSIONS = ['cpg', 'dbf', 'prj', 'shp', 'shx']
DEFAULT_KEYWORDS = {'mmw', 'model-my-watershed'}
MMW_APP_KEY_FLAG = '{"appkey": "model-my-watershed"}'


@shared_task
def create_resource(user_id, title, abstract, keywords):
    hs = hss.get_client(user_id)

    # Convert keywords from array to set of values
    keywords = set(keywords) if keywords else set()

    # POST new resource creates it in HydroShare
    resource = hs.createResource(
        'CompositeResource',
        title,
        abstract=abstract,
        keywords=tuple(DEFAULT_KEYWORDS | keywords),
        extra_metadata=MMW_APP_KEY_FLAG,
    )

    return resource


@shared_task
def add_file(resource, user_id, f, overwrite=False):
    hs = hss.get_client(user_id)

    hs.add_file(resource, f, overwrite)

    return resource


@shared_task
def add_shapefile(resource, user_id, aoi_json):
    hs = hss.get_client(user_id)

    crs = {'no_defs': True, 'proj': 'longlat',
           'ellps': 'WGS84', 'datum': 'WGS84'}
    schema = {'geometry': aoi_json['type'], 'properties': {}}
    with fiona.open('/tmp/{}.shp'.format(resource), 'w',
                    driver='ESRI Shapefile',
                    crs=crs, schema=schema) as shapefile:
        shapefile.write({'geometry': aoi_json, 'properties': {}})

    for ext in SHAPEFILE_EXTENSIONS:
        filename = '/tmp/{}.{}'.format(resource, ext)
        with open(filename) as shapefile:
            hs.addResourceFile(resource, shapefile,
                               'area-of-interest.{}'.format(ext))
        os.remove(filename)

    return resource


@shared_task
def add_metadata(resource, user_id):
    hs = hss.get_client(user_id)

    # Make resource public and shareable
    endpoint = hs.resource(resource)
    endpoint.public(True)
    endpoint.shareable(True)

    # Add geographic coverage
    endpoint.functions.set_file_type({
        'file_path': 'area-of-interest.shp',
        'hs_file_type': 'GeoFeature',
    })

    return resource


@shared_task
def link_to_project_and_save(resource, project_id, title, autosync):
    project = Project.objects.get(pk=project_id)

    # Link HydroShareResrouce to Project and save
    hsresource = HydroShareResource.objects.create(
        project=project,
        resource=resource,
        title=title,
        autosync=autosync,
        exported_at=now()
    )
    hsresource.save()

    # Make Project public and save
    project.is_private = False
    project.save()

    # Return newly created HydroShareResource
    serializer = HydroShareResourceSerializer(hsresource)
    return serializer.data


@shared_task
def update_hydroshare_resource(_, project_id):
    hsresource = HydroShareResource.objects.get(project_id=project_id)

    hsresource.exported_at = now()
    hsresource.save()

    serializer = HydroShareResourceSerializer(hsresource)
    return serializer.data
