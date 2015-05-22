import json
import os
import pkgutil
import sys
import yaml
import bootstrap_cfn.errors as errors
import bootstrap_cfn.utils as utils

from copy import deepcopy


class ProjectConfig:

    config = None

    def __init__(self, config, environment, passwords=None):
        self.config = self.load_yaml(config)[environment]
        if passwords:
            passwords_dict = self.load_yaml(passwords)[environment]
            self.config = utils.dict_merge(self.config, passwords_dict)

    @staticmethod
    def load_yaml(fp):
        if os.path.exists(fp):
            return yaml.load(open(fp).read())


class ConfigParser:

    config = {}

    def __init__(self, data, stack_name):
        self.stack_name = stack_name
        self.data = data

    def process(self):
        from troposphere import Output, GetAtt

        vpc = self.vpc()
        iam = self.iam()
        ec2 = self.ec2()
        rds = {}
        s3 = {}
        elb = {}

        if 'rds' in self.data:
            rds = self.rds()
        if 's3' in self.data:
            s3 = self.s3()
        if 'elb' in self.data:
            elb, elb_sgs = self.elb()
            # GET LIST OF ELB NAMES AND ADD TO EC2 INSTANCES
            elb_name_list = []
            for i in elb:
                if i.keys()[0][0:3] == "ELB":
                    elb_name_list.append(
                        i[i.keys()[0]]['Properties']['LoadBalancerName'])
            ec2['ScalingGroup']['Properties'][
                'LoadBalancerNames'] = elb_name_list

        # LOAD BASE TEMPLATE AND INSERT AWS SERVICES

        template = self.base_template()

        if 'rds' in self.data:
            template.add_output(Output(
                "dbhost",
                Description="RDS Hostname",
                Value=GetAtt("RDSInstance", "Endpoint.Address")
            ))
            template.add_output(Output(
                "dbport",
                Description="RDS Port",
                Value=GetAtt("RDSInstance", "Endpoint.Port")
            ))

        # Convert to a data structure. Later this should be removed once we
        # remove the 'json' include
        template = json.loads(template.to_json())

        template['Resources'].update(iam)
        template['Resources'].update(vpc)
        template['Resources'].update(ec2)
        template['Resources'].update(rds)
        template['Resources'].update(s3)

        for i in elb:
            template['Resources'].update(i)
        if 'elb_sgs' in locals():
            for k, v in elb_sgs.items():
                template['Resources'][k] = v

        if 'includes' in self.data:
            for inc_path in self.data['includes']:
                inc = json.load(open(inc_path))
                template = utils.dict_merge(template, inc)
        return json.dumps(
            template, sort_keys=True, indent=4, separators=(',', ': '))

    def base_template(self):
        from troposphere import Template

        t = Template()

        t.add_mapping("AWSRegion2AMI", {
            "eu-west-1": {"AMI": "ami-f0b11187"},
        })

        if 'vpc' in self.data:
            t.add_mapping("SubnetConfig", {
                "VPC": self.data['vpc']
            })
        else:
            t.add_mapping("SubnetConfig", {
                "VPC": {
                    "CIDR": "10.0.0.0/16",
                    "SubnetA": "10.0.0.0/20",
                    "SubnetB": "10.0.16.0/20",
                    "SubnetC": "10.0.32.0/20"
                }
            })

        return t

    def vpc(self):
        from troposphere import Ref, FindInMap, Tags, awsencode
        from troposphere.ec2 import Route, Subnet, InternetGateway, VPC, VPCGatewayAttachment, SubnetRouteTableAssociation, RouteTable

        vpc = VPC(
            "VPC",
            InstanceTenancy="default",
            EnableDnsSupport="true",
            CidrBlock=FindInMap("SubnetConfig", "VPC", "CIDR"),
            EnableDnsHostnames="true",
        )

        subnet_a = Subnet(
            "SubnetA",
            VpcId=Ref(vpc),
            AvailabilityZone="eu-west-1a",
            CidrBlock=FindInMap("SubnetConfig", "VPC", "SubnetA"),
            Tags=Tags(
                Application=Ref("AWS::StackId"),
                Network="Public",
            ),
        )

        subnet_b = Subnet(
            "SubnetB",
            VpcId=Ref(vpc),
            AvailabilityZone="eu-west-1b",
            CidrBlock=FindInMap("SubnetConfig", "VPC", "SubnetB"),
            Tags=Tags(
                Application=Ref("AWS::StackId"),
                Network="Public",
            ),
        )

        subnet_c = Subnet(
            "SubnetC",
            VpcId=Ref(vpc),
            AvailabilityZone="eu-west-1c",
            CidrBlock=FindInMap("SubnetConfig", "VPC", "SubnetC"),
            Tags=Tags(
                Application=Ref("AWS::StackId"),
                Network="Public",
            ),
        )

        igw = InternetGateway(
            "InternetGateway",
            Tags=Tags(
                Application=Ref("AWS::StackId"),
                Network="Public",
            ),
        )

        gw_attachment = VPCGatewayAttachment(
            "AttachGateway",
            VpcId=Ref(vpc),
            InternetGatewayId=Ref(igw),
        )

        route_table = RouteTable(
            "PublicRouteTable",
            VpcId=Ref(vpc),
            Tags=Tags(
                Application=Ref("AWS::StackId"),
                Network="Public",
            ),
        )

        public_route = Route(
            "PublicRoute",
            GatewayId=Ref(igw),
            DestinationCidrBlock="0.0.0.0/0",
            RouteTableId=Ref(route_table),
            DependsOn=gw_attachment.title
        )

        subnet_a_route_assoc = SubnetRouteTableAssociation(
            "SubnetRouteTableAssociationA",
            SubnetId=Ref(subnet_a),
            RouteTableId=Ref(route_table),
        )

        subnet_b_route_assoc = SubnetRouteTableAssociation(
            "SubnetRouteTableAssociationB",
            SubnetId=Ref(subnet_b),
            RouteTableId=Ref(route_table),
        )

        subnet_c_route_assoc = SubnetRouteTableAssociation(
            "SubnetRouteTableAssociationC",
            SubnetId=Ref(subnet_c),
            RouteTableId=Ref(route_table),
        )

        resources = [vpc, subnet_a, subnet_b, subnet_c, igw, gw_attachment,
                     public_route, route_table, subnet_a_route_assoc,
                     subnet_b_route_assoc, subnet_c_route_assoc]

        # Hack until we return troposphere objects directly
        return json.loads(json.dumps(dict((r.title, r) for r in resources), cls=awsencode))

    def iam(self):
        from troposphere import Ref, awsencode
        from troposphere.iam import Role, PolicyType, InstanceProfile
        role = Role(
            "BaseHostRole",
            Path="/",
            AssumeRolePolicyDocument={
                "Statement": [{
                    "Action": ["sts:AssumeRole"],
                    "Effect": "Allow",
                    "Principal": {"Service": ["ec2.amazonaws.com"]}
                }]
            },
        )

        role_policies = PolicyType(
            "RolePolicies",
            PolicyName="BaseHost",
            PolicyDocument={"Statement": [
                {"Action": ["autoscaling:Describe*"], "Resource": "*", "Effect": "Allow"},
                {"Action": ["ec2:Describe*"], "Resource": "*", "Effect": "Allow"},
                {"Action": ["rds:Describe*"], "Resource": "*", "Effect": "Allow"},
                {"Action": ["elasticloadbalancing:Describe*"], "Resource": "*", "Effect": "Allow"},
                {"Action": ["elasticache:Describe*"], "Resource": "*", "Effect": "Allow"},
                {"Action": ["cloudformation:Describe*"], "Resource": "*", "Effect": "Allow"},
                {"Action": ["s3:List*"], "Resource": "*", "Effect": "Allow"}
            ]},
            Roles=[Ref(role)],
        )
        instance_profile = InstanceProfile(
            "InstanceProfile",
            Path="/",
            Roles=[Ref(role)],
        )

        resources = [role, role_policies, instance_profile]
        # Hack until we return troposphere objects directly
        return json.loads(json.dumps(dict((r.title, r) for r in resources), cls=awsencode))

    def s3(self):
        # REQUIRED FIELDS AND MAPPING
        required_fields = {
            'static-bucket-name': 'BucketName'
        }

        # TEST FOR REQUIRED FIELDS AND EXIT IF MISSING ANY
        present_keys = self.data['s3'].keys()
        for i in required_fields.keys():
            if i not in present_keys:
                print "\n\n[ERROR] Missing S3 fields [%s]" % i
                sys.exit(1)

        from troposphere import Ref, awsencode
        from troposphere.s3 import Bucket, BucketPolicy

        bucket = Bucket(
            "StaticBucket",
            AccessControl="BucketOwnerFullControl",
            BucketName=self.data['s3']['static-bucket-name'],
        )

        if 'policy' in present_keys:
            policy = json.loads(open(self.data['s3']['policy']).read())
        else:
            arn = 'arn:aws:s3:::%s/*' % self.data['s3']['static-bucket-name']
            policy = {
                'Action': [
                    's3:Get*',
                    's3:Put*',
                    's3:List*'],
                'Resource': arn,
                'Effect': 'Allow',
                'Principal': {
                    'AWS': '*'}}

        bucket_policy = BucketPolicy(
            "StaticBucketPolicy",
            Bucket=Ref(bucket),
            PolicyDocument={"Statement": [policy]},
        )

        resources = [bucket, bucket_policy]
        # Hack until we return troposphere objects directly
        return json.loads(json.dumps(dict((r.title, r) for r in resources), cls=awsencode))

    def ssl(self):
        return self.data['ssl']

    def rds(self):
        # REQUIRED FIELDS MAPPING
        required_fields = {
            'db-name': 'DBName',
            'storage': 'AllocatedStorage',
            'storage-type': 'StorageType',
            'backup-retention-period': 'BackupRetentionPeriod',
            'db-master-username': 'MasterUsername',
            'db-master-password': 'MasterUserPassword',
            'identifier': 'DBInstanceIdentifier',
            'db-engine': 'Engine',
            'db-engine-version': 'EngineVersion',
            'instance-class': 'DBInstanceClass',
            'multi-az': 'MultiAZ'
        }

        optional_fields = {
            'storage-encrypted': 'StorageEncrypted',
        }

        # LOAD STACK TEMPLATE
        from troposphere import Ref, FindInMap, GetAtt, awsencode
        from troposphere.rds import DBInstance, DBSubnetGroup
        from troposphere.ec2 import SecurityGroup
        resources = []
        rds_subnet_group = DBSubnetGroup(
            "RDSSubnetGroup",
            SubnetIds=[Ref("SubnetA"), Ref("SubnetB"), Ref("SubnetC")],
            DBSubnetGroupDescription="VPC Subnets"
        )
        resources.append(rds_subnet_group)

        database_sg = SecurityGroup(
            "DatabaseSG",
            SecurityGroupIngress=[
                {"ToPort": 5432,
                 "FromPort": 5432,
                 "IpProtocol": "tcp",
                 "CidrIp": FindInMap("SubnetConfig", "VPC", "CIDR")},
                {"ToPort": 3306,
                 "FromPort": 3306,
                 "IpProtocol": "tcp",
                 "CidrIp": FindInMap("SubnetConfig", "VPC", "CIDR")}
            ],
            VpcId=Ref("VPC"),
            GroupDescription="SG for EC2 Access to RDS",
        )
        resources.append(database_sg)

        rds_instance = DBInstance(
            "RDSInstance",
            PubliclyAccessible=False,
            AllowMajorVersionUpgrade=False,
            AutoMinorVersionUpgrade=False,
            VPCSecurityGroups=[GetAtt(database_sg, "GroupId")],
            DBSubnetGroupName=Ref(rds_subnet_group),
            StorageEncrypted=False,
            DependsOn=database_sg.title
        )
        resources.append(rds_instance)

        # TEST FOR REQUIRED FIELDS AND EXIT IF MISSING ANY
        for yaml_key, rds_prop in required_fields.iteritems():
            if yaml_key not in self.data['rds']:
                print "\n\n[ERROR] Missing RDS fields [%s]" % yaml_key
                sys.exit(1)
            else:
                rds_instance.__setattr__(rds_prop, self.data['rds'][yaml_key])

        for yaml_key, rds_prop in optional_fields.iteritems():
            if yaml_key in self.data['rds']:
                rds_instance.__setattr__(rds_prop, self.data['rds'][yaml_key])

        # Hack until we return troposphere objects directly
        return json.loads(json.dumps(dict((r.title, r) for r in resources), cls=awsencode))

    def elb(self):
        # REQUIRED FIELDS AND MAPPING
        required_fields = {
            'listeners': 'Listeners',
            'scheme': 'Scheme',
            'name': 'LoadBalancerName',
            'hosted_zone': 'HostedZoneName'
        }

        from troposphere import Ref, Join, GetAtt, awsencode
        from troposphere.elasticloadbalancing import LoadBalancer, HealthCheck, ConnectionDrainingPolicy
        from troposphere.ec2 import SecurityGroup
        from troposphere.iam import PolicyType
        from troposphere.route53 import RecordSetGroup, RecordSet, AliasTarget

        elb_list = []
        elb_sgs = []
        # COULD HAVE MULTIPLE ELB'S (PUBLIC / PRIVATE etc)
        for elb in self.data['elb']:
            safe_name = elb['name'].replace('-', '').replace('.', '').replace('_', '')
            # TEST FOR REQUIRED FIELDS AND EXIT IF MISSING ANY
            for i in required_fields.keys():
                if i not in elb.keys():
                    print "\n\n[ERROR] Missing ELB fields [%s]" % i
                    sys.exit(1)

            load_balancer = LoadBalancer(
                "ELB" + safe_name,
                Subnets=[Ref("SubnetA"), Ref("SubnetB"), Ref("SubnetC")],
                Listeners=elb['listeners'],
                Scheme=elb['scheme'],
                LoadBalancerName='ELB-%s' % elb['name'].replace('.', ''),
                ConnectionDrainingPolicy=ConnectionDrainingPolicy(
                    Enabled=True,
                    Timeout=120,
                ),
            )

            if "health_check" in elb:
                load_balancer.HealthCheck = HealthCheck(**elb['health_check'])

            for listener in load_balancer.Listeners:
                if listener['Protocol'] == 'HTTPS':
                    try:
                        cert_name = elb['certificate_name']
                    except KeyError:
                        raise errors.CfnConfigError(
                            "HTTPS listener but no certificate_name specified")
                    try:
                        self.ssl()[cert_name]['cert']
                        self.ssl()[cert_name]['key']
                    except KeyError:
                        raise errors.CfnConfigError(
                            "Couldn't find ssl cert {0} in config file".format(cert_name))

                    listener["SSLCertificateId"] = Join("", [
                        "arn:aws:iam::",
                        Ref("AWS::AccountId"),
                        ":server-certificate/",
                        "{0}-{1}".format(cert_name, self.stack_name)]
                    )

            elb_list.append(load_balancer)

            dns_record = RecordSetGroup(
                "DNS" + safe_name,
                HostedZoneName=elb['hosted_zone'],
                Comment="Zone apex alias targeted to ElasticLoadBalancer.",
                RecordSets=[
                    RecordSet(
                        "TitleIsIgnoredForThisResource",
                        Name="%s.%s" % (elb['name'], elb['hosted_zone']),
                        Type="A",
                        AliasTarget=AliasTarget(
                            GetAtt(load_balancer, "CanonicalHostedZoneNameID"),
                            GetAtt(load_balancer, "DNSName"),
                        ),
                    ),
                ]
            )
            elb_list.append(dns_record)

            elb_role_policies = PolicyType(
                "Policy" + safe_name,
                PolicyName=safe_name+"BaseHost",
                PolicyDocument={"Statement": [{
                    "Action": [
                        "elasticloadbalancing:DeregisterInstancesFromLoadBalancer",
                        "elasticloadbalancing:RegisterInstancesWithLoadBalancer"
                    ],
                    "Resource": [
                        Join("", [
                            "arn:aws:elasticloadbalancing:",
                            Ref("AWS::Region"),
                            ":",
                            Ref("AWS::AccountId"),
                            ':loadbalancer/%s' % load_balancer.LoadBalancerName
                            ])
                    ],
                    "Effect": "Allow"}
                ]},
                Roles=[Ref("BaseHostRole")],
            )
            elb_list.append(elb_role_policies)

            if "security_groups" in elb:
                load_balancer.SecurityGroups = []
                for sg_name, sg_rules in elb['security_groups'].items():
                    sg = SecurityGroup(
                        sg_name,
                        GroupDescription=sg_name,
                        SecurityGroupIngress=sg_rules,
                        VpcId=Ref("VPC")
                    )
                    load_balancer.SecurityGroups.append(Ref(sg))
                    elb_sgs.append(sg)
            else:
                sg = SecurityGroup(
                    "DefaultSG" + safe_name,
                    GroupDescription="DefaultELBSecurityGroup",
                    SecurityGroupIngress=[
                        {
                            "IpProtocol": "tcp",
                            "FromPort": 443,
                            "ToPort": 443,
                            "CidrIp": "0.0.0.0/0"
                            },
                        {
                            "IpProtocol": "tcp",
                            "FromPort": 80,
                            "ToPort": 80,
                            "CidrIp": "0.0.0.0/0"
                        }
                    ],
                    VpcId=Ref("VPC")
                )
                load_balancer.SecurityGroups = [Ref(sg)]
                elb_sgs.append(sg)

        # Hack until we return troposphere objects directly
        elb_sg_dict = {}
        for sg in elb_sgs:
            elb_sg_dict[sg.title] = json.loads(json.dumps(sg, cls=awsencode))

        resources = []
        for res in elb_list:
            resources.append({res.title: json.loads(json.dumps(res, cls=awsencode))})
        return resources, elb_sg_dict

    def ec2(self):
        # LOAD STACK TEMPLATE
        from troposphere import Ref, FindInMap, GetAZs, Base64, Join, awsencode
        from troposphere.ec2 import SecurityGroup
        from troposphere.autoscaling import LaunchConfiguration, \
            AutoScalingGroup, BlockDeviceMapping, EBSBlockDevice, Tag

        resources = []
        sgs = []

        for sg_name, ingress in self.data['ec2']['security_groups'].items():
            sg = SecurityGroup(
                sg_name,
                VpcId=Ref("VPC"),
                GroupDescription="BaseHost Security Group",
                SecurityGroupIngress=ingress
            )
            sgs.append(sg)
            resources.append(sg)

        devices = []
        try:
            for i in self.data['ec2']['block_devices']:
                devices.append(BlockDeviceMapping(
                    DeviceName=i['DeviceName'],
                    Ebs=EBSBlockDevice(VolumeSize=i['VolumeSize']),
                ))
        except KeyError:
            devices.append(BlockDeviceMapping(
                DeviceName="/dev/sda1",
                Ebs=EBSBlockDevice(VolumeSize=20),
            ))

        launch_config = LaunchConfiguration(
            "BaseHostLaunchConfig",
            KeyName=self.data['ec2']['parameters']['KeyName'],
            SecurityGroups=[Ref(g) for g in sgs],
            InstanceType=self.data['ec2']['parameters']['InstanceType'],
            AssociatePublicIpAddress=True,
            IamInstanceProfile=Ref("InstanceProfile"),
            ImageId=FindInMap("AWSRegion2AMI", Ref("AWS::Region"), "AMI"),
            BlockDeviceMappings=devices,
            UserData=Base64(Join("", [
                "#!/bin/bash -xe\n",
                "#do nothing for now",
            ])),
        )
        resources.append(launch_config)

        scaling_group = AutoScalingGroup(
            "ScalingGroup",
            VPCZoneIdentifier=[Ref("SubnetA"), Ref("SubnetB"), Ref("SubnetC")],
            MinSize=self.data['ec2']['auto_scaling']['min'],
            MaxSize=self.data['ec2']['auto_scaling']['max'],
            DesiredCapacity=self.data['ec2']['auto_scaling']['desired'],
            AvailabilityZones=GetAZs(),
            Tags=[Tag(k, v, True) for k, v in self.data['ec2']['tags'].iteritems()],
            LaunchConfigurationName=Ref(launch_config),
        )
        resources.append(scaling_group)

        # Hack until we return troposphere objects directly
        return json.loads(json.dumps(dict((r.title, r) for r in resources), cls=awsencode))
