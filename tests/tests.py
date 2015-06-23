#!/usr/bin/env python

import unittest
from bootstrap_cfn.config import ProjectConfig, ConfigParser
from troposphere import awsencode, Ref
from troposphere import iam, s3, rds, ec2
from troposphere import Base64, FindInMap, GetAtt, GetAZs, Join
from troposphere.autoscaling import AutoScalingGroup, Tag
from troposphere.ec2 import SecurityGroup
from troposphere.autoscaling import LaunchConfiguration

from troposphere.route53 import RecordSetGroup
from troposphere.elasticloadbalancing import LoadBalancer, HealthCheck,\
    ConnectionDrainingPolicy
from troposphere.iam import PolicyType

import bootstrap_cfn.errors as errors
from testfixtures import compare
import json


class TestConfig(unittest.TestCase):

    def setUp(self):
        pass

    def test_project_config(self):
        '''
        Test the file is valid YAML and takes and environment
        '''
        config = ProjectConfig('tests/sample-project.yaml', 'dev')
        self.assertEquals(
            sorted(
                config.config.keys()), [
                'ec2', 'elb', 'rds', 's3', 'ssl'])

    def test_project_config_merge_password(self):
        '''
        Test the two config files merge properly by ensuring elements from both files are present
        '''
        config = ProjectConfig(
            'tests/sample-project.yaml',
            'dev',
            'tests/sample-project-passwords.yaml')
        self.assertEquals(
            config.config['rds']['instance-class'],
            'db.t2.micro')
        self.assertEquals(
            config.config['rds']['db-master-password'],
            'testpassword')


class TestConfigParser(unittest.TestCase):

    def setUp(self):
        self.maxDiff = 9000

    def _resources_to_dict(self, resources):
        return json.loads(json.dumps(
            dict((r.title, r) for r in resources),
            cls=awsencode)
        )

    def test_iam(self):
        basehost_role = iam.Role('BaseHostRole')
        basehost_role.Path = '/'
        basehost_role.AssumeRolePolicyDocument = {
            'Statement': [{'Action': ['sts:AssumeRole'],
                           'Effect': 'Allow',
                           'Principal': {'Service': ['ec2.amazonaws.com']}}
                          ]
        }
        basehost_role_ref = iam.Ref(basehost_role)

        role_policy = iam.PolicyType('RolePolicies')
        role_policy.PolicyName = 'BaseHost'
        role_policy.PolicyDocument = {
            'Statement': [
                {'Action': ['autoscaling:Describe*'],
                 'Resource': '*',
                 'Effect': 'Allow'},
                {'Action': ['ec2:Describe*'],
                 'Resource': '*',
                 'Effect': 'Allow'},
                {'Action': ['rds:Describe*'],
                 'Resource': '*',
                 'Effect': 'Allow'},
                {'Action': ['elasticloadbalancing:Describe*'],
                 'Resource': '*',
                 'Effect': 'Allow'},
                {'Action': ['elasticache:Describe*'],
                 'Resource': '*',
                 'Effect': 'Allow'},
                {'Action': ['cloudformation:Describe*'],
                 'Resource': '*',
                 'Effect': 'Allow'},
                {'Action': ['s3:List*'],
                 'Resource': '*',
                 'Effect': 'Allow'}]
        }
        role_policy.Roles = [basehost_role_ref]

        instance_profile = iam.InstanceProfile('InstanceProfile')
        instance_profile.Path = '/'
        instance_profile.Roles = [basehost_role_ref]

        config = ConfigParser(None, 'my-stack-name')
        known = [role_policy, instance_profile, basehost_role]
        known = self._resources_to_dict(known)
        iam_dict = self._resources_to_dict(config.iam())
        compare(known, iam_dict)

    def test_s3(self):
        bucket = s3.Bucket('StaticBucket')
        bucket.AccessControl = 'BucketOwnerFullControl'
        bucket.BucketName = 'moj-test-dev-static'
        bucket_ref = Ref(bucket)

        static_bp = s3.BucketPolicy('StaticBucketPolicy')
        static_bp.PolicyDocument = {
            'Statement': [
                {
                    'Action': [
                        's3:GetObject'],
                    'Resource': 'arn:aws:s3:::moj-test-dev-static/*',
                    'Effect': 'Allow',
                    'Principal': '*'
                }
            ]
        }
        static_bp.Bucket = bucket_ref

        config = ConfigParser(
            ProjectConfig(
                'tests/sample-project.yaml',
                'dev').config,
            'my-stack-name')
        compare(self._resources_to_dict([static_bp, bucket]),
                self._resources_to_dict(config.s3()))

    def test_custom_s3_policy(self):
        expected_s3 = [
            {
                'Action': [
                    's3:Get*',
                    's3:Put*',
                    's3:List*',
                    's3:Delete*'],
                'Resource': 'arn:aws:s3:::moj-test-dev-static/*',
                            'Effect': 'Allow',
                            'Principal': {'AWS': '*'}
            }
        ]

        project_config = ProjectConfig('tests/sample-project.yaml', 'dev')

        project_config.config['s3'] = {
            'static-bucket-name': 'moj-test-dev-static',
            'policy': 'tests/sample-custom-s3-policy.json'}

        config = ConfigParser(project_config.config, 'my-stack-name')
        s3_cfg = self._resources_to_dict(config.s3())
        s3_custom_cfg = s3_cfg['StaticBucketPolicy'][
            'Properties']['PolicyDocument']['Statement']

        compare(expected_s3, s3_custom_cfg)

    def test_rds(self):
        db_sg = ec2.SecurityGroup('DatabaseSG')
        db_sg.VpcId = Ref('VPC')
        db_sg.GroupDescription = 'SG for EC2 Access to RDS'
        db_sg.SecurityGroupIngress = [
            {"ToPort": 5432,
             "FromPort": 5432,
             "IpProtocol": "tcp",
             "CidrIp": FindInMap("SubnetConfig", "VPC", "CIDR")},
            {"ToPort": 3306,
             "FromPort": 3306,
             "IpProtocol": "tcp",
             "CidrIp": FindInMap("SubnetConfig", "VPC", "CIDR")}
        ]

        db_subnet = rds.DBSubnetGroup('RDSSubnetGroup')
        db_subnet.SubnetIds = [Ref('SubnetA'), Ref('SubnetB'), Ref('SubnetC')]
        db_subnet.DBSubnetGroupDescription = 'VPC Subnets'

        db_instance = rds.DBInstance('RDSInstance', DependsOn=db_sg.title)
        db_instance.MultiAZ = False
        db_instance.MasterUsername = 'testuser'
        db_instance.MasterUserPassword = 'testpassword'
        db_instance.DBName = 'test'
        db_instance.PubliclyAccessible = False
        db_instance.StorageEncrypted = False
        db_instance.StorageType = 'gp2'
        db_instance.AllocatedStorage = 5
        db_instance.AllowMajorVersionUpgrade = False
        db_instance.AutoMinorVersionUpgrade = False
        db_instance.BackupRetentionPeriod = 1
        db_instance.DBInstanceClass = 'db.t2.micro'
        db_instance.DBInstanceIdentifier = 'test-dev'
        db_instance.Engine = 'postgres'
        db_instance.EngineVersion = '9.3.5'
        db_instance.VPCSecurityGroups = [GetAtt(db_sg, 'GroupId')]
        db_instance.DBSubnetGroupName = Ref(db_subnet)

        known = [db_instance, db_subnet, db_sg]

        config = ConfigParser(
            ProjectConfig(
                'tests/sample-project.yaml',
                'dev',
                'tests/sample-project-passwords.yaml').config, 'my-stack-name')
        rds_dict = self._resources_to_dict(config.rds())
        known = self._resources_to_dict(known)
        compare(known, rds_dict)

    def test_elb(self):
        known = []
        lb = LoadBalancer(
            "ELBtestdevinternal",
            ConnectionDrainingPolicy=ConnectionDrainingPolicy(
                Enabled=True,
                Timeout=120,
            ),
            Subnets=[Ref("SubnetA"), Ref("SubnetB"), Ref("SubnetC")],
            Listeners=[
                {
                    "InstancePort": 80,
                    "LoadBalancerPort": 80,
                    "Protocol": "TCP"
                }
            ],
            SecurityGroups=[Ref("DefaultSGtestdevinternal")],
            LoadBalancerName="ELB-test-dev-internal",
            Scheme="internal",
        )

        pt1 = PolicyType(
            "Policytestdevexternal",
            PolicyName="testdevexternalBaseHost",
            PolicyDocument={
                "Statement": [
                    {
                        "Action":
                            ["elasticloadbalancing:DeregisterInstancesFromLoadBalancer",
                             "elasticloadbalancing:RegisterInstancesWithLoadBalancer"],
                        "Resource": [
                            Join(
                                "", ["arn:aws:elasticloadbalancing:",
                                     Ref("AWS::Region"), ":",
                                     Ref("AWS::AccountId"),
                                     ":loadbalancer/ELB-test-dev-external"]
                            )],
                        "Effect": "Allow"}
                ]
            },
            Roles=[Ref("BaseHostRole")],
        )

        pt2 = PolicyType(
            "Policytestdevinternal",
            PolicyName="testdevinternalBaseHost",
            PolicyDocument={
                "Statement": [
                    {
                        "Action":
                            ["elasticloadbalancing:DeregisterInstancesFromLoadBalancer",
                             "elasticloadbalancing:RegisterInstancesWithLoadBalancer"],
                        "Resource":
                            [
                                Join("", ["arn:aws:elasticloadbalancing:",
                                          Ref("AWS::Region"), ":",
                                          Ref("AWS::AccountId"),
                                          ":loadbalancer/ELB-test-dev-internal"]
                                     )
                            ],
                        "Effect": "Allow"
                    }
                ]
            },
            Roles=[Ref("BaseHostRole")],
        )

        rs = RecordSetGroup(
            "DNStestdevexternal",
            Comment="Zone apex alias targeted to ElasticLoadBalancer.",
            HostedZoneName="kyrtest.pf.dsd.io.",
            RecordSets=[
                {
                    "Type": "A",
                    "AliasTarget": {
                        "HostedZoneId": GetAtt("ELBtestdevexternal",
                                               "CanonicalHostedZoneNameID"),
                        "DNSName": GetAtt("ELBtestdevexternal", "DNSName")
                    },
                    "Name": "test-dev-external.kyrtest.pf.dsd.io."
                }
            ],
        )

        rsg = RecordSetGroup(
            "DNStestdevinternal",
            Comment="Zone apex alias targeted to ElasticLoadBalancer.",
            HostedZoneName="kyrtest.pf.dsd.io.",
            RecordSets=[
                {
                    "Type": "A",
                    "AliasTarget": {
                        "HostedZoneId":
                            GetAtt(lb, "CanonicalHostedZoneNameID"),
                        "DNSName": GetAtt(lb, "DNSName")
                    },
                    "Name": "test-dev-internal.kyrtest.pf.dsd.io."
                }
            ],
        )

        lb2 = LoadBalancer(
            "ELBtestdevexternal",
            ConnectionDrainingPolicy=ConnectionDrainingPolicy(
                Enabled=True,
                Timeout=120,
            ),
            Subnets=[Ref("SubnetA"), Ref("SubnetB"), Ref("SubnetC")],
            Listeners=[
                {"InstancePort": 80,
                 "LoadBalancerPort": 80,
                 "Protocol": "TCP"},
                {"InstancePort": 443,
                 "LoadBalancerPort": 443,
                 "Protocol": "TCP"}
            ],
            SecurityGroups=[Ref("DefaultSGtestdevexternal")],
            LoadBalancerName="ELB-test-dev-external",
            Scheme="internet-facing",
        )
        known = [lb, lb2, pt1, pt2, rs, rsg]
        expected_sgs = [
            SecurityGroup(
                "DefaultSGtestdevexternal",
                SecurityGroupIngress=[
                    {
                        "ToPort": 443,
                        "IpProtocol": "tcp",
                        "FromPort": 443,
                        "CidrIp": "0.0.0.0/0"
                    },
                    {"ToPort": 80,
                     "IpProtocol": "tcp",
                     "FromPort": 80,
                     "CidrIp": "0.0.0.0/0"
                     }
                ],
                VpcId=Ref("VPC"),
                GroupDescription="DefaultELBSecurityGroup"),
            SecurityGroup(
                "DefaultSGtestdevinternal",
                SecurityGroupIngress=[
                    {"ToPort": 443,
                     "IpProtocol": "tcp",
                     "FromPort": 443,
                     "CidrIp": "0.0.0.0/0"
                     },
                    {
                        "ToPort": 80,
                        "IpProtocol": "tcp",
                        "FromPort": 80,
                        "CidrIp": "0.0.0.0/0"
                    }
                ],
                VpcId=Ref("VPC"),
                GroupDescription="DefaultELBSecurityGroup",
            )]

        config = ConfigParser(
            ProjectConfig(
                'tests/sample-project.yaml',
                'dev').config, 'my-stack-name')
        elb_cfg, elb_sgs = config.elb()

        compare(self._resources_to_dict(known),
                self._resources_to_dict(elb_cfg))

        compare(self._resources_to_dict(expected_sgs),
                self._resources_to_dict(elb_sgs))

    def test_elb_custom_sg(self):

        expected_sgs = {
            'SGName': {
                'Properties': {
                    u'SecurityGroupIngress': [
                        {'ToPort': 443,
                         'IpProtocol': 'tcp',
                         'CidrIp': '1.2.3.4/32',
                         'FromPort': 443},
                    ],
                    'VpcId': {'Ref': 'VPC'},
                    'GroupDescription': 'SGName'
                },
                'Type': u'AWS::EC2::SecurityGroup',
            },
        }

        project_config = ProjectConfig('tests/sample-project.yaml', 'dev')

        # Remove the "test-dev-internal" ELB
        project_config.config['elb'] = [{
            'name': 'test-dev-external',
            'hosted_zone': 'kyrtest.pf.x',
            'scheme': 'internet-facing',
            'listeners': [
                {'LoadBalancerPort': 443,
                 'InstancePort': 443,
                 'Protocol': 'TCP'}
            ],
            'security_groups': {
                'SGName': [
                    {'IpProtocol': 'tcp',
                     'FromPort': 443,
                     'ToPort': 443,
                     'CidrIp': '1.2.3.4/32'},
                ]
            }
        }]

        config = ConfigParser(project_config.config, 'my-stack-name')
        elb_cfg,  elb_sgs = config.elb()
        elb_dict = self._resources_to_dict(elb_cfg)
        sgs_dict = self._resources_to_dict(elb_sgs)
        compare(expected_sgs, sgs_dict)

        # elb = [e.values()[0] for e in elb_dict if 'ELBtestdevexternal' in e]
        compare(elb_dict['ELBtestdevexternal']['Properties']['SecurityGroups'],
                [{u'Ref': u'SGName'}])

    def test_cf_includes(self):
        project_config = ProjectConfig('tests/sample-project.yaml',
                                       'dev',
                                       'tests/sample-project-passwords.yaml')
        project_config.config['includes'] = ['tests/sample-include.json']
        known_outputs = {
            "dbhost": {
                "Description": "RDS Hostname",
                "Value": {"Fn::GetAtt": ["RDSInstance", "Endpoint.Address"]}
            },
            "dbport": {
                "Description": "RDS Port",
                "Value": {"Fn::GetAtt": ["RDSInstance", "Endpoint.Port"]}
            },
            "someoutput": {
                "Description": "For tests",
                "Value": "BLAHBLAH"
            }
        }
        config = ConfigParser(project_config.config, 'my-stack-name')
        cfg = json.loads(config.process())
        outputs = cfg['Outputs']
        compare(known_outputs, outputs)

    def test_process(self):
        """
        This isn't the best test, but we at least check that we have the right
        Resource names returned
        """
        project_config = ProjectConfig(
            'tests/sample-project.yaml',
            'dev',
            'tests/sample-project-passwords.yaml')
        config = ConfigParser(project_config.config, 'my-stack-name')

        cfn_template = json.loads(config.process())

        wanted = [
            "AnotherSG", "AttachGateway", "BaseHostLaunchConfig",
            "BaseHostRole", "BaseHostSG", "DNStestdevexternal",
            "DNStestdevinternal", "DatabaseSG", "DefaultSGtestdevexternal",
            "DefaultSGtestdevinternal", "ELBtestdevexternal",
            "ELBtestdevinternal", "InstanceProfile", "InternetGateway",
            "Policytestdevexternal", "Policytestdevinternal", "PublicRoute",
            "PublicRouteTable", "RDSInstance", "RDSSubnetGroup",
            "RolePolicies", "ScalingGroup", "StaticBucket",
            "StaticBucketPolicy", "SubnetA", "SubnetB", "SubnetC",
            "SubnetRouteTableAssociationA", "SubnetRouteTableAssociationB",
            "SubnetRouteTableAssociationC", "VPC"
        ]

        resource_names = cfn_template['Resources'].keys()
        resource_names.sort()
        compare(resource_names, wanted)

        wanted = ["dbhost", "dbport"]
        output_names = cfn_template['Outputs'].keys()
        output_names.sort()
        compare(output_names, wanted)

        mappings = cfn_template['Mappings']
        expected = {
            'AWSRegion2AMI': {'eu-west-1': {'AMI': 'ami-f0b11187'}},
            'SubnetConfig': {
                'VPC': {
                    'CIDR': '10.0.0.0/16',
                    'SubnetA': '10.0.0.0/20',
                    'SubnetB': '10.0.16.0/20',
                    'SubnetC': '10.0.32.0/20',
                }
            }
        }
        compare(mappings, expected)

    def test_process_with_vpc_config(self):
        """
        This isn't the best test, but we at least check that we have the right
        Resource names returned
        """
        project_config = ProjectConfig(
            'tests/sample-project.yaml',
            'dev',
            'tests/sample-project-passwords.yaml')
        project_config.config['vpc'] = {
            'CIDR': '172.22.0.0/16',
            'SubnetA': '172.22.1.0/24',
            'SubnetB': '172.22.2.0/24',
            'SubnetC': '172.22.3.0/24',
        }
        config = ConfigParser(project_config.config, 'my-stack-name')

        cfn_template = json.loads(config.process())

        wanted = [
            "AnotherSG", "AttachGateway", "BaseHostLaunchConfig",
            "BaseHostRole", "BaseHostSG", "DNStestdevexternal",
            "DNStestdevinternal", "DatabaseSG", "DefaultSGtestdevexternal",
            "DefaultSGtestdevinternal", "ELBtestdevexternal",
            "ELBtestdevinternal", "InstanceProfile", "InternetGateway",
            "Policytestdevexternal", "Policytestdevinternal", "PublicRoute",
            "PublicRouteTable", "RDSInstance", "RDSSubnetGroup",
            "RolePolicies", "ScalingGroup", "StaticBucket",
            "StaticBucketPolicy", "SubnetA", "SubnetB", "SubnetC",
            "SubnetRouteTableAssociationA", "SubnetRouteTableAssociationB",
            "SubnetRouteTableAssociationC", "VPC"
        ]

        resource_names = cfn_template['Resources'].keys()
        resource_names.sort()
        compare(resource_names, wanted)

        wanted = ["dbhost", "dbport"]
        output_names = cfn_template['Outputs'].keys()
        output_names.sort()
        compare(output_names, wanted)

        mappings = cfn_template['Mappings']
        expected = {
            'AWSRegion2AMI': {'eu-west-1': {'AMI': 'ami-f0b11187'}},
            'SubnetConfig': {
                'VPC': {
                    'CIDR': '172.22.0.0/16',
                    'SubnetA': '172.22.1.0/24',
                    'SubnetB': '172.22.2.0/24',
                    'SubnetC': '172.22.3.0/24',
                }
            }
        }
        compare(mappings, expected)

    def test_process_no_elbs_no_rds(self):
        project_config = ProjectConfig('tests/sample-project.yaml', 'dev')
        # Assuming there's no ELB defined
        project_config.config.pop('elb')
        project_config.config.pop('rds')
        config = ConfigParser(project_config.config, 'my-stack-name')
        config.process()

    def test_elb_missing_cert(self):

        self.maxDiff = None
        project_config = ProjectConfig('tests/sample-project.yaml', 'dev')
        # Ugh. Fixtures please?
        project_config.config.pop('ssl')
        project_config.config['elb'] = [{
            'name': 'docker-registry.service',
            'hosted_zone': 'kyrtest.foo.bar.',
            'certificate_name': 'my-cert',
            'scheme': 'internet-facing',
            'listeners': [
                {'LoadBalancerPort': 80,
                 'InstancePort': 80,
                 'Protocol': 'TCP'
                 },
                {'LoadBalancerPort': 443,
                 'InstancePort': 443,
                 'Protocol': 'HTTPS'
                 },
            ],
        }]
        config = ConfigParser(project_config.config, 'my-stack-name')
        with self.assertRaises(errors.CfnConfigError):
            config.elb()

    def test_elb_missing_cert_name(self):

        self.maxDiff = None
        project_config = ProjectConfig('tests/sample-project.yaml', 'dev')
        # Ugh. Fixtures please?
        project_config.config['elb'] = [{
            'name': 'docker-registry.service',
            'hosted_zone': 'kyrtest.foo.bar.',
            'scheme': 'internet-facing',
            'listeners': [
                {'LoadBalancerPort': 80,
                 'InstancePort': 80,
                 'Protocol': 'TCP'
                 },
                {'LoadBalancerPort': 443,
                 'InstancePort': 443,
                 'Protocol': 'HTTPS'
                 },
            ],
        }]
        config = ConfigParser(project_config.config, 'my-stack-name')
        with self.assertRaises(errors.CfnConfigError):
            config.elb()

    def test_elb_with_ssl(self):

        self.maxDiff = None
        DNSdockerregistryservice = RecordSetGroup(
            "DNSdockerregistryservice",
            Comment="Zone apex alias targeted to ElasticLoadBalancer.",
            HostedZoneName="kyrtest.foo.bar.",
            RecordSets=[
                {"Type": "A",
                 "AliasTarget": {
                     "HostedZoneId": GetAtt("ELBdockerregistryservice",
                                            "CanonicalHostedZoneNameID"),
                     "DNSName": GetAtt("ELBdockerregistryservice", "DNSName")
                 },
                 "Name": "docker-registry.service.kyrtest.foo.bar."
                 }
            ],
        )

        ELBdockerregistryservice = LoadBalancer(
            "ELBdockerregistryservice",
            ConnectionDrainingPolicy=ConnectionDrainingPolicy(
                Enabled=True,
                Timeout=120,
            ),
            Subnets=[Ref("SubnetA"), Ref("SubnetB"), Ref("SubnetC")],
            Listeners=[
                {"InstancePort": 80, "LoadBalancerPort": 80, "Protocol": "TCP"},
                {"InstancePort": 443, "SSLCertificateId": Join(
                    "", ["arn:aws:iam::", Ref("AWS::AccountId"),
                         ":server-certificate/", "my-cert-my-stack-name"]),
                 "LoadBalancerPort": 443, "Protocol": "HTTPS"}],
            SecurityGroups=[Ref("DefaultSGdockerregistryservice")],
            LoadBalancerName="ELB-docker-registryservice",
            Scheme="internet-facing",
        )

        Policydockerregistryservice = PolicyType(
            "Policydockerregistryservice",
            PolicyName="dockerregistryserviceBaseHost",
            PolicyDocument={
                "Statement": [
                    {
                        "Action": [
                            "elasticloadbalancing:DeregisterInstancesFromLoadBalancer",
                            "elasticloadbalancing:RegisterInstancesWithLoadBalancer"
                        ],
                        "Resource": [
                            Join("", ["arn:aws:elasticloadbalancing:",
                                      Ref("AWS::Region"), ":",
                                      Ref("AWS::AccountId"),
                                      ":loadbalancer/ELB-docker-registryservice"
                                      ]
                                 )
                        ],
                        "Effect": "Allow"
                    }
                ]
            },
            Roles=[Ref("BaseHostRole")],
        )
        known = [DNSdockerregistryservice, ELBdockerregistryservice,
                 Policydockerregistryservice]

        project_config = ProjectConfig('tests/sample-project.yaml', 'dev')
        # Ugh. Fixtures please?
        project_config.config['elb'] = [{
            'name': 'docker-registry.service',
            'hosted_zone': 'kyrtest.foo.bar.',
            'scheme': 'internet-facing',
            'certificate_name': 'my-cert',
            'listeners': [
                {'LoadBalancerPort': 80,
                 'InstancePort': 80,
                 'Protocol': 'TCP'
                 },
                {'LoadBalancerPort': 443,
                 'InstancePort': 443,
                 'Protocol': 'HTTPS'
                 },
            ],
        }]
        config = ConfigParser(project_config.config, 'my-stack-name')
        elb_cfg, _ = config.elb()
        # elb_dict = json.loads(json.dumps([{r.title: r} for r in elb_cfg ],
        #                                  cls=awsencode))
        compare(self._resources_to_dict(known),
                self._resources_to_dict(elb_cfg))

    def test_elb_with_healthcheck(self):
        self.maxDiff = None

        DNSdockerregistryservice = RecordSetGroup(
            "DNSdockerregistryservice",
            Comment="Zone apex alias targeted to ElasticLoadBalancer.",
            HostedZoneName="kyrtest.foo.bar.",
            RecordSets=[
                {
                    "Type": "A",
                    "AliasTarget": {
                        "HostedZoneId": GetAtt("ELBdockerregistryservice",
                                               "CanonicalHostedZoneNameID"),
                        "DNSName": GetAtt("ELBdockerregistryservice", "DNSName")
                    },
                    "Name": "docker-registry.service.kyrtest.foo.bar."
                }
            ],
        )

        ELBdockerregistryservice = LoadBalancer(
            "ELBdockerregistryservice",
            ConnectionDrainingPolicy=ConnectionDrainingPolicy(
                Enabled=True,
                Timeout=120,
            ),
            Subnets=[Ref("SubnetA"), Ref("SubnetB"), Ref("SubnetC")],
            HealthCheck=HealthCheck(
                HealthyThreshold=10,
                Interval=2,
                Target="HTTPS:80/blah",
                Timeout=5,
                UnhealthyThreshold=2,
            ),
            Listeners=[{"InstancePort": 80,
                        "LoadBalancerPort": 80,
                        "Protocol": "TCP"},
                       {"InstancePort": 443,
                        "LoadBalancerPort": 443,
                        "Protocol": "TCP"}
                       ],
            SecurityGroups=[Ref("DefaultSGdockerregistryservice")],
            LoadBalancerName="ELB-docker-registryservice",
            Scheme="internet-facing",
        )

        Policydockerregistryservice = PolicyType(
            "Policydockerregistryservice",
            PolicyName="dockerregistryserviceBaseHost",
            PolicyDocument={
                "Statement": [
                    {
                        "Action":
                            ["elasticloadbalancing:DeregisterInstancesFromLoadBalancer",
                             "elasticloadbalancing:RegisterInstancesWithLoadBalancer"],
                        "Resource": [
                            Join("", ["arn:aws:elasticloadbalancing:",
                                      Ref("AWS::Region"), ":",
                                      Ref("AWS::AccountId"),
                                      ":loadbalancer/ELB-docker-registryservice"]
                                 )],
                        "Effect": "Allow"
                    }
                ]

            },
            Roles=[Ref("BaseHostRole")],
        )
        project_config = ProjectConfig('tests/sample-project.yaml', 'dev')
        project_config.config['elb'] = [
            {
                'name': 'docker-registry.service',
                'hosted_zone': 'kyrtest.foo.bar.',
                'scheme': 'internet-facing',
                'listeners': [
                    {'LoadBalancerPort': 80,
                     'InstancePort': 80,
                     'Protocol': 'TCP'
                     },
                    {'LoadBalancerPort': 443,
                     'InstancePort': 443,
                     'Protocol': 'TCP'
                     },
                    ],
                'health_check': {
                    'HealthyThreshold': 10,
                    'Interval': 2,
                    'Target': 'HTTPS:80/blah',
                    'Timeout': 5,
                    'UnhealthyThreshold': 2
                }
            }
        ]
        config = ConfigParser(project_config.config, 'my-stack-name')
        elb_cfg, _ = config.elb()
        known = [DNSdockerregistryservice, ELBdockerregistryservice,
                 Policydockerregistryservice]
        compare(self._resources_to_dict(known),
                self._resources_to_dict(elb_cfg))

    def test_elb_with_reserved_chars(self):
        ELBdevdockerregistryservice = LoadBalancer(
            "ELBdevdockerregistryservice",
            ConnectionDrainingPolicy=ConnectionDrainingPolicy(
                Enabled=True,
                Timeout=120,
            ),
            Subnets=[Ref("SubnetA"), Ref("SubnetB"), Ref("SubnetC")],
            Listeners=[
                {"InstancePort": 80,
                 "LoadBalancerPort": 80,
                 "Protocol": "TCP"},
                {"InstancePort": 443,
                 "LoadBalancerPort": 443,
                 "Protocol": "TCP"
                 }
            ],
            SecurityGroups=[Ref("DefaultSGdevdockerregistryservice")],
            LoadBalancerName="ELB-dev_docker-registryservice",
            Scheme="internet-facing",
        )

        DNSdevdockerregistryservice = RecordSetGroup(
            "DNSdevdockerregistryservice",
            Comment="Zone apex alias targeted to ElasticLoadBalancer.",
            HostedZoneName="kyrtest.foo.bar.",
            RecordSets=[
                {"Type": "A",
                 "AliasTarget": {
                     "HostedZoneId":
                         GetAtt(ELBdevdockerregistryservice,
                                "CanonicalHostedZoneNameID"),
                     "DNSName": GetAtt(ELBdevdockerregistryservice,
                                       "DNSName")
                 },
                 "Name": "dev_docker-registry.service.kyrtest.foo.bar."
                 }
            ],
        )

        Policydevdockerregistryservice = PolicyType(
            "Policydevdockerregistryservice",
            PolicyName="devdockerregistryserviceBaseHost",
            PolicyDocument={
                "Statement":
                    [
                        {
                            "Action": [
                                "elasticloadbalancing:DeregisterInstancesFromLoadBalancer",
                                "elasticloadbalancing:RegisterInstancesWithLoadBalancer"],
                            "Resource": [
                                Join("",
                                     ["arn:aws:elasticloadbalancing:",
                                      Ref("AWS::Region"), ":",
                                      Ref("AWS::AccountId"),
                                      ":loadbalancer/ELB-dev_docker-registryservice"]
                                     )
                            ],
                            "Effect": "Allow"
                        }
                    ]
            },
            Roles=[Ref("BaseHostRole")],
        )

        project_config = ProjectConfig('tests/sample-project.yaml', 'dev')
        # Ugh. Fixtures please?
        project_config.config['elb'] = [{
            'name': 'dev_docker-registry.service',
            'hosted_zone': 'kyrtest.foo.bar.',
            'scheme': 'internet-facing',
            'listeners': [
                {'LoadBalancerPort': 80,
                 'InstancePort': 80,
                 'Protocol': 'TCP'
                 },
                {'LoadBalancerPort': 443,
                 'InstancePort': 443,
                 'Protocol': 'TCP'
                 },
            ],
        }]
        config = ConfigParser(project_config.config, 'my-stack-name')
        elb_cfg, _ = config.elb()
        known = [DNSdevdockerregistryservice, ELBdevdockerregistryservice,
                 Policydevdockerregistryservice]
        compare(self._resources_to_dict(known),
                self._resources_to_dict(elb_cfg))

    def test_ec2(self):

        self.maxDiff = None

        tags = [
            ('Role', 'docker'),
            ('Apps', 'test'),
            ]
        ScalingGroup = AutoScalingGroup(
            "ScalingGroup",
            DesiredCapacity=1,
            Tags=[Tag(k, v, True) for (k, v) in tags],
            MinSize=0,
            MaxSize=3,
            VPCZoneIdentifier=[Ref("SubnetA"), Ref("SubnetB"), Ref("SubnetC")],
            LaunchConfigurationName=Ref("BaseHostLaunchConfig"),
            AvailabilityZones=GetAZs(""),
        )

        BaseHostSG = SecurityGroup(
            "BaseHostSG",
            SecurityGroupIngress=[
                {
                    "ToPort": 22,
                    "FromPort": 22,
                    "IpProtocol": "tcp",
                    "CidrIp": "0.0.0.0/0"
                },
                {
                    "ToPort": 80,
                    "FromPort": 80,
                    "IpProtocol": "tcp",
                    "CidrIp": "0.0.0.0/0"
                }
            ],
            VpcId=Ref("VPC"),
            GroupDescription="BaseHost Security Group",
        )

        BaseHostLaunchConfig = LaunchConfiguration(
            "BaseHostLaunchConfig",
            UserData=Base64(Join("", ["#!/bin/bash -xe\n", "#do nothing for now"])),
            ImageId=FindInMap("AWSRegion2AMI", Ref("AWS::Region"), "AMI"),
            BlockDeviceMappings=[
                {
                    "DeviceName": "/dev/sda1",
                    "Ebs": {"VolumeSize": 10}
                },
                {
                    "DeviceName": "/dev/sdf",
                    "Ebs": {"VolumeSize": 10}
                }
            ],
            KeyName="default",
            SecurityGroups=[Ref(BaseHostSG), Ref("AnotherSG")],
            IamInstanceProfile=Ref("InstanceProfile"),
            InstanceType="t2.micro",
            AssociatePublicIpAddress="true",
        )

        AnotherSG = SecurityGroup(
            "AnotherSG",
            SecurityGroupIngress=[
                {
                    "ToPort": 443,
                    "FromPort": 443,
                    "SourceSecurityGroupName": Ref(BaseHostSG),
                    "IpProtocol": "tcp"
                }
            ],
            VpcId=Ref("VPC"),
            GroupDescription="BaseHost Security Group",
        )
        known = [AnotherSG, BaseHostLaunchConfig, BaseHostSG, ScalingGroup]
        config = ConfigParser(
            ProjectConfig(
                'tests/sample-project.yaml',
                'dev').config, 'my-stack-name')
        ec2_json = self._resources_to_dict(config.ec2())
        # compare(, ec2_json)

        compare(self._resources_to_dict(known), ec2_json)

    def test_ec2_with_no_block_device_specified(self):
        project_config = ProjectConfig('tests/sample-project.yaml', 'dev')
        project_config.config['ec2'].pop('block_devices')
        config = ConfigParser(project_config.config, 'my-stack-name')
        ec2_dict = self._resources_to_dict(config.ec2())
        config_output = ec2_dict['BaseHostLaunchConfig'][
            'Properties']['BlockDeviceMappings']
        known = [{'DeviceName': '/dev/sda1', 'Ebs': {'VolumeSize': 20}}]
        self.assertEquals(known, config_output)

if __name__ == '__main__':
    unittest.main()
