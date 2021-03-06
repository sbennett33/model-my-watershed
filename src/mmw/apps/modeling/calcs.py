# -*- coding: utf-8 -*-
from __future__ import print_function
from __future__ import unicode_literals
from __future__ import absolute_import

import json

from copy import deepcopy

from django.conf import settings
from django.db import connection

from django.contrib.gis.geos import WKBReader


HECTARES_PER_SQM = 0.0001


def split_into_huc12s(code, id):
    layer = _get_boundary_layer_by_code(code)
    if not layer or 'table_name' not in layer:
        raise ValueError('Layer not supported: ', code)

    table_name = layer.get('table_name')
    huc_code = table_name.split('_')[1]

    sql = '''
          SELECT 'huc12__' || boundary_huc12.id,
                 boundary_huc12.huc12,
                 ST_AsGeoJSON(boundary_huc12.geom_detailed)
          FROM boundary_huc12, {table_name}
          WHERE huc12 LIKE ({huc_code} || '%%')
          AND {table_name}.id = %s
          '''.format(table_name=table_name, huc_code=huc_code)

    with connection.cursor() as cursor:
        cursor.execute(sql, [int(id)])
        return cursor.fetchall()


def get_huc12s(huc12_ids):
    """
    Fetch the name and shapes of a list
    of huc12 codes.
    :param huc12_ids: a list of twelve-digit HUC-12 ids
    :return: a dictionary keyed on the HUC-12 ids containing
             the HUC-12 names and geoms
             { <huc12_id>: { name: <huc12_name>,
                             shape: <huc12 GeoJSON geometry> }}
    """
    sql = '''
          SELECT huc12,
                 name,
                 ST_AsGeoJSON(geom_detailed)
          FROM boundary_huc12
          WHERE huc12 IN %s
          '''
    with connection.cursor() as cursor:
        cursor.execute(sql, [tuple(huc12_ids)])
        rows = cursor.fetchall()
        return [{'id': row[0], 'name': row[1], 'shape': json.loads(row[2])}
                for row in rows]


def get_catchments(comids):
    sql = '''
          SELECT comid,
                 ST_Area(ST_Transform(geom_catch, 5070)),
                 ST_AsGeoJSON(geom_catch),
                 ST_AsGeoJSON(geom_stream)
          FROM nhdpluscatchment
          WHERE comid in %s
          '''
    with connection.cursor() as cursor:
        cursor.execute(sql, [tuple(comids)])
        rows = cursor.fetchall()
        return [{'id': row[0], 'area': row[1] * HECTARES_PER_SQM,
                 'shape': json.loads(row[2]), 'stream': json.loads(row[3])}
                for row in rows]


def apply_gwlfe_modifications(gms, modifications):
    # Partition modifications into array and key modifications.
    # Array modifications target gms arrays, and have keys like
    # {array}__{index}. Key modifications target gms keys and
    # can be used by simply updating modified_gms.
    array_mods = []
    key_mods = []
    modified_gms = deepcopy(gms)

    for mod in modifications:
        for key, value in mod.iteritems():
            if '__' in key:
                array_mods.append({key: value})
            else:
                key_mods.append({key: value})

    for mod in array_mods:
        for key, value in mod.iteritems():
            gmskey, i = key.split('__')
            modified_gms[gmskey][int(i)] = value

    for mod in key_mods:
        modified_gms.update(mod)

    return modified_gms


def apply_subbasin_gwlfe_modifications(gms, modifications,
                                       total_stream_lengths):
    ag_stream_length_weighted_keys = ['n43', 'n45', 'n46c']
    urban_stream_length_weighted_keys = ['UrbBankStab']
    weighted_modifications = deepcopy(modifications)

    # Weight factors for this subbasin's stream length given
    # total stream length in the AoI
    try:
        ag_pct_total_stream_length = (gms['AgLength'] /
                                      total_stream_lengths['ag'])
    except ZeroDivisionError:
        ag_pct_total_stream_length = 1

    try:
        urban_pct_total_stream_length = ((gms['StreamLength'] -
                                          gms['AgLength']) /
                                         total_stream_lengths['urban'])
    except ZeroDivisionError:
        urban_pct_total_stream_length = 1

    for mod in weighted_modifications:
        for key, val in mod.iteritems():
            if key in ag_stream_length_weighted_keys:
                val *= ag_pct_total_stream_length
            elif key in urban_stream_length_weighted_keys:
                val *= urban_pct_total_stream_length
            mod[key] = val

    return apply_gwlfe_modifications(gms, weighted_modifications)


def sum_subbasin_stream_lengths(gmss):
    ag = sum([gms['AgLength'] for gms in gmss.itervalues()])
    urban = sum([gms['StreamLength'] - gms['AgLength']
                 for gms in gmss.itervalues()])

    return {
        'ag': ag,
        'urban': urban
    }


def get_layer_shape(table_code, id):
    """
    Fetch shape of well known area of interest.

    :param table_code: Code of table
    :param id: ID of the shape
    :return: GeoJSON of shape if found, None otherwise
    """
    layer = _get_boundary_layer_by_code(table_code)
    if not layer:
        return None

    table = layer['table_name']
    field = layer.get('json_field', 'geom')
    properties = ''

    if table.startswith('boundary_huc'):
        properties = "'huc', {}".format(table[-5:])

    sql = '''
          SELECT json_build_object(
            'type', 'Feature',
            'id', id,
            'geometry', ST_AsGeoJSON({field})::json,
            'properties', json_build_object({properties}))
          FROM {table}
          WHERE id = %s
          '''.format(field=field, properties=properties, table=table)

    with connection.cursor() as cursor:
        cursor.execute(sql, [int(id)])
        row = cursor.fetchone()

        if row:
            return row[0]
        else:
            return None


def boundary_search_context(search_term):
    suggestions = [] if len(search_term) < 3 else \
        _do_boundary_search(search_term)
    # Data format should match the ArcGIS API suggest endpoint response
    return {
        'suggestions': suggestions,
    }


def _get_boundary_search_query(search_term):
    """
    Return raw SQL query to perform full text search against
    all searchable boundary layers.
    """
    select_fmt = """
        (SELECT id, '{code}' AS code, name, {rank} AS rank,
            ST_Centroid(geom) as center
        FROM {table}
        WHERE UPPER(name) LIKE UPPER(%(term)s)
        LIMIT 3)
    """.strip()

    selects = []
    for layer in settings.LAYER_GROUPS['boundary']:
        if not layer.get('searchable'):
            continue

        code = layer['code']
        table_name = layer['table_name']
        rank = layer.get('search_rank', 0)

        selects.append(select_fmt.format(
            code=code, table=table_name, rank=rank))

    if len(selects) == 0:
        raise Exception('No boundary layers are searchable')

    subquery = ' UNION ALL '.join(selects)

    return """
        SELECT id, code, name, rank, center
        FROM ({}) AS subquery
        ORDER BY rank DESC, name
    """.format(subquery)


def _do_boundary_search(search_term):
    """
    Execute full text search against all searchable boundary layers.
    """
    result = []
    query = _get_boundary_search_query(search_term)

    with connection.cursor() as cursor:
        wildcard_term = '%{}%'.format(search_term)
        cursor.execute(query, {'term': wildcard_term})

        wkb_r = WKBReader()

        for row in cursor.fetchall():
            id = row[0]
            code = row[1]
            name = row[2]
            rank = row[3]
            point = wkb_r.read(row[4])

            layer = _get_boundary_layer_by_code(code)

            result.append({
                'id': id,
                'code': code,
                'text': name,
                'label': layer['short_display'],
                'rank': rank,
                'y': point.y,
                'x': point.x,
            })

    return result


def _get_boundary_layer_by_code(code):
    for layer in settings.LAYER_GROUPS['boundary']:
        if layer.get('code') == code:
            return layer
    return False
