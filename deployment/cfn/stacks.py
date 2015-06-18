from majorkirby import GlobalConfigNode

from vpc import VPC
from s3_vpc_endpoint import S3VPCEndpoint
from private_hosted_zone import PrivateHostedZone
from data_plane import DataPlane
from cache_private_dns_record import CachePrivateDNSRecord
from application import Application
from tiler import Tiler
from tile_delivery_network import TileDeliveryNetwork
from worker import Worker

import ConfigParser


def get_config(mmw_config_path, profile):
    """Parses a configuration file

    Arguments
    :param mmw_config_path: Path to the config file
    :param profile: Config profile to read
    """
    mmw_config = ConfigParser.ConfigParser()
    mmw_config.optionxform = str
    mmw_config.read(mmw_config_path)

    return {k: v.strip('"').strip("'") for k, v in mmw_config.items(profile)}


def build_graph(mmw_config, aws_profile, **kwargs):
    """
    Builds graphs for all of the MMW stacks
    Args:
      mmw_config (dict): dictionary representation of `default.yml`
      aws_profile (str): name of AWS profile to use for authentication
    """

    if kwargs['stack_color'] is not None:
        mmw_config['StackColor'] = kwargs['stack_color'].capitalize()

    global_config = GlobalConfigNode(**mmw_config)
    vpc = VPC(globalconfig=global_config, aws_profile=aws_profile)
    s3_vpc_endpoint = S3VPCEndpoint(globalconfig=global_config, VPC=vpc,
                                    aws_profile=aws_profile)
    private_hosted_zone = PrivateHostedZone(globalconfig=global_config,
                                            VPC=vpc, aws_profile=aws_profile)
    data_plane = DataPlane(globalconfig=global_config, VPC=vpc,
                           PrivateHostedZone=private_hosted_zone,
                           aws_profile=aws_profile)
    cache_private_dns_record = CachePrivateDNSRecord(
        globalconfig=global_config,
        PrivateHostedZone=private_hosted_zone, DataPlane=data_plane,
        aws_profile=aws_profile
    )

    tiler = Tiler(globalconfig=global_config, VPC=vpc, aws_profile=aws_profile)
    tile_delivery_network = TileDeliveryNetwork(globalconfig=global_config,
                                                VPC=vpc,
                                                PrivateHostedZone=private_hosted_zone,  # NOQA
                                                aws_profile=aws_profile)
    application = Application(globalconfig=global_config, VPC=vpc,
                              TileDeliveryNetwork=tile_delivery_network,
                              aws_profile=aws_profile)
    worker = Worker(globalconfig=global_config, VPC=vpc,
                    aws_profile=aws_profile)

    return s3_vpc_endpoint, cache_private_dns_record, tiler, application, \
        worker


def build_stacks(mmw_config, aws_profile, **kwargs):
    """Trigger actual building of graphs"""
    s3_vpc_endpoint_graph, cache_private_dns_record_graph, tiler_graph, \
        application_graph, worker_graph = build_graph(mmw_config, aws_profile,
                                                      **kwargs)
    s3_vpc_endpoint_graph.go()
    cache_private_dns_record_graph.go()

    if kwargs['stack_color'] is not None:
        tiler_graph.go()
        application_graph.go()
        worker_graph.go()
