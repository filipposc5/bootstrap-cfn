#!/usr/bin/env python

import sys
import urllib2
import json
import random
import yaml
import time
from bootstrap_cfn.config import AWSConfig, ProjectConfig, ConfigParser
from bootstrap_cfn.cloudformation import Cloudformation
from bootstrap_cfn.ec2 import EC2
from bootstrap_cfn.iam import IAM

import os

from fabric.api import env, task, sudo, execute, run, parallel, settings
from fabric.contrib.project import rsync_project, upload_project
from fabric.operations import put

# GLOBAL VARIABLES
env.application = None
env.environment = None
env.aws = None
env.config = None
env.password = None
TIMEOUT = 3600
RETRY_INTERVAL = 10

# This is needed because pkgutil wont pick up modules
# imported in a fabfile.
path = env.real_fabfile or os.getcwd()
sys.path.append(os.path.dirname(path))
@task
def aws(x):
    env.aws = str(x).lower()


@task
def environment(x):
    env.environment = str(x).lower()


@task
def application(x):
    env.application = str(x).lower()


@task
def config(x):
    env.config = str(x).lower()


@task
def passwords(x):
    env.stack_passwords = str(x).lower()


@task
def blocking(x):
    env.blocking = str(x).lower()


@task
def user(x):
    env.user = x

def get_stack_name():
    if hasattr(env, 'stack_name'):
        return env.stack_name
    return "%s-%s" % (env.application, env.environment)

def get_config():
    if env.aws is None:
        print "\n[ERROR] Please specify an AWS account, e.g 'aws:dev'"
        sys.exit(1)
    if env.environment is None:
        print "\n[ERROR] Please specify an environment, e.g 'environment:dev'"
        sys.exit(1)
    if env.application is None:
        print "\n[ERROR] Please specify an application, e.g 'application:peoplefinder'"
        sys.exit(1)
    if env.config is None:
        print "\n[ERROR] Please specify a config file, e.g 'config:/tmp/sample-application.yaml'"
        sys.exit(1)

    if not hasattr(env, 'stack_passwords'):
        env.stack_passwords={}
    aws_config = AWSConfig(env.aws)
    project_config = ProjectConfig(
        env.config,
        env.environment,
        passwords=env.stack_passwords)

    cfn_config = ConfigParser(project_config.config, get_stack_name())
    cfn = Cloudformation(aws_config)
    return aws_config, cfn, cfn_config

@task
def cfn_delete(force=False):
    if not force:
        x = raw_input("Are you really sure you want to blow away the whole stack!? (y/n)\n")
        if not x in ['y','Y','Yes','yes']:
            sys.exit(1)
    stack_name = get_stack_name()
    aws_config, cfn, cfn_config = get_config()
    cfn.delete(stack_name)
    print "\n\nSTACK {0} DELETING...".format(stack_name)

    if hasattr(env, 'blocking') and env.blocking.lower() == 'false':
        print stacks
        print 'Running in non blocking mode. Exiting.'
        sys.exit(0)

    # Wait for stacks to delete
    print 'Waiting for stack to delete.'
    cfn.wait_for_stack_missing(stack_name)
    print "Stack successfully deleted"
    if 'ssl' in cfn_config.data:
        iam = IAM(aws_config)
        iam.delete_ssl_certificate(cfn_config.ssl(), stack_name)

@task
def cfn_create():
    stack_name = get_stack_name()
    aws_config, cfn, cfn_config = get_config()
    #Upload any SSL certs that we may need for the stack.
    if 'ssl' in cfn_config.data:
        iam = IAM(aws_config)
        iam.upload_ssl_certificate(cfn_config.ssl(), stack_name)
    #Useful for debug
    #print cfn_config.process()
    # Inject security groups in stack template and create stacks.
    stack = cfn.create(stack_name, cfn_config.process())
    print "\n\nSTACK {0} CREATING...".format(stack_name)

    if hasattr(env, 'blocking') and env.blocking.lower() == 'false':
        print stacks
        print 'Running in non blocking mode. Exiting.'
        sys.exit(0)

    # Wait for stacks to complete
    print 'Waiting for stack to complete.'
    cfn.wait_for_stack_done(stack)
    print 'Stacks completed, checking results.'
    stack_evt = cfn.get_last_stack_event(stack)
    print '{0}: {1}'.format(stack_evt.stack_name, stack_evt.resource_status)
    if stack_evt.resource_status == 'CREATE_COMPLETE':
        print 'Successfully built stack {0}.'.format(stack)
    else:
        print 'Failed to create stack: {0}'.format(stack)
        #So delete the SSL cert that we uploaded
        if 'ssl' in cfn_config.data:
            iam = IAM(aws_config)
            iam.delete_ssl_certificate(cfn_config.ssl(), stack_name)

def get_stack_instances_ips(stack_name):
    if env.aws is None:
        print "\n[ERROR] Please specify an AWS account, e.g 'aws:dev'"
        sys.exit(1)

    aws_config = AWSConfig(env.aws)
    cfn = Cloudformation(aws_config)
    ec2 = EC2(aws_config)
    instance_id_list = cfn.get_stack_instance_ids(stack_name)
    return ec2.get_instance_public_ips(instance_id_list)


@task
def get_stack_addresses():
    if env.environment is None:
        print "\n[ERROR] Please specify an environment, e.g 'environment:dev'"
        sys.exit(1)
    if env.application is None:
        print "\n[ERROR] Please specify an application, e.g 'application:peoplefinder'"
        sys.exit(1)
    stack_name = get_stack_name()
    res = get_stack_instances_ips(stack_name)
    print res
    return res


@task
def find_master():
    stack_name = get_stack_name()
    aws_config, cfn, cfn_config = get_config()
    ec2 = EC2(aws_config)
    stack_name = get_stack_name()
    master = ec2.get_master_instance(stack_name).ip_address
    print 'Salt master public address: {0}'.format(master)
    return master


def get_stack_instances_ips(stack_name):
    stack_name = get_stack_name()
    aws_config, cfn, cfn_config = get_config()
    ec2 = EC2(aws_config)
    instance_id_list = cfn.get_stack_instance_ids(stack_name)


def get_candidate_minions():
    stack_name = get_stack_name()
    aws_config, cfn, cfn_config = get_config()
    ec2 = EC2(aws_config)
    instance_ids = cfn.get_stack_instance_ids(stack_name)
    stack_name = get_stack_name()
    master_instance_id = ec2.get_master_instance(stack_name).id
    instance_ids.remove(master_instance_id)
    return instance_ids


@task
def install_minions():
    stack_name = get_stack_name()
    aws_config, cfn, cfn_config = get_config()
    ec2 = EC2(aws_config)
    print "Waiting for SSH on all instances..."
    ec2.wait_for_ssh(stack_name)
    candidates = get_candidate_minions()
    existing_minions = ec2.get_minions(stack_name)
    to_install = list(set(candidates).difference(set(existing_minions)))
    if not to_install:
        return
    public_ips = ec2.get_instance_public_ips(to_install)
    sha = '6080a18e6c7c2d49335978fa69fa63645b45bc2a'
    stack_name = get_stack_name()
    master_inst = ec2.get_master_instance(stack_name)
    master_public_ip = master_inst.ip_address
    master_prv_ip = master_inst.private_ip_address
    ec2.set_instance_tags(to_install, {'SaltMasterPrvIP': master_prv_ip})
    for inst_ip in public_ips:
        env.host_string = 'ubuntu@%s' % inst_ip
        sudo('wget https://raw.githubusercontent.com/ministryofjustice/bootstrap-cfn/master/scripts/bootstrap-salt.sh -O /tmp/moj-bootstrap.sh')
        sudo('chmod 755 /tmp/moj-bootstrap.sh')
        sudo('/tmp/moj-bootstrap.sh')
        sudo(
            'wget https://raw.githubusercontent.com/saltstack/salt-bootstrap/%s/bootstrap-salt.sh -O /tmp/bootstrap-salt.sh' %
            sha)
        sudo('chmod 755 /tmp/bootstrap-salt.sh')
        sudo(
            '/tmp/bootstrap-salt.sh -A `cat /etc/tags/SaltMasterPrvIP` git v2014.1.4')
        env.host_string = 'ubuntu@%s' % master_public_ip
        sudo('salt-key -y -A')

@task
def install_master():
    stack_name = get_stack_name()
    aws_config, cfn, cfn_config = get_config()
    ec2 = EC2(aws_config)
    print "Waiting for SSH on all instances..."
    ec2.wait_for_ssh(stack_name)
    instance_ids = cfn.get_stack_instance_ids(stack_name)
    master_inst = ec2.get_master_instance(stack_name)
    master = master_inst.id if master_inst else random.choice(instance_ids)
    master_prv_ip = ec2.get_instance_private_ips([master])[0]
    master_public_ip = ec2.get_instance_public_ips([master])[0]
    ec2.set_instance_tags(instance_ids, {'SaltMasterPrvIP': master_prv_ip})
    ec2.set_instance_tags(master, {'SaltMaster': 'True'})

    stack_ips = ec2.get_instance_private_ips(instance_ids)
    stack_ips.remove(master_prv_ip)
    stack_public_ips = ec2.get_instance_public_ips(instance_ids)
    stack_public_ips.remove(master_public_ip)
    env.host_string = 'ubuntu@%s' % master_public_ip
    sha = '6080a18e6c7c2d49335978fa69fa63645b45bc2a'
    sudo('wget https://raw.githubusercontent.com/ministryofjustice/bootstrap-cfn/master/scripts/bootstrap-salt.sh -O /tmp/moj-bootstrap.sh')
    sudo('chmod 755 /tmp/moj-bootstrap.sh')
    sudo('/tmp/moj-bootstrap.sh')
    sudo(
        'wget https://raw.githubusercontent.com/saltstack/salt-bootstrap/%s/bootstrap-salt.sh -O /tmp/bootstrap-salt.sh' %
        sha)
    sudo('chmod 755 /tmp/bootstrap-salt.sh')
    sudo(
        '/tmp/bootstrap-salt.sh -M -A `cat /etc/tags/SaltMasterPrvIP` git v2014.1.4')
    sudo('salt-key -y -A')

    master_instance = ec2.get_instance_by_id(master)
    sg_name = '{0}-salt_sg'.format(stack_name)
    try:
        salt_sg = ec2.get_sg(sg_name)
    except Exception:
        salt_sg = ec2.create_sg(sg_name)
    existing_hosts = [x.cidr_ip for rule in salt_sg.rules for x in rule.grants]
    print existing_hosts
    for prv_ip in stack_ips:
        print '\t %s/32' % prv_ip, '{0}/32'.format(prv_ip) not in existing_hosts
        if '{0}/32'.format(prv_ip) not in existing_hosts:
            ec2.add_minion_to_sg(salt_sg, prv_ip)
    groups = master_instance.get_attribute('groupSet').get('groupSet')
    groups.append(salt_sg)
    master_instance.modify_attribute('groupSet', [x.id for x in groups])


@task
def rsync():
    if env.aws is None:
        print "\n[ERROR] Please specify an AWS account, e.g 'aws:dev'"
        sys.exit(1)
    if env.environment is None:
        print "\n[ERROR] Please specify an environment, e.g 'environment:dev'"
        sys.exit(1)
    if env.application is None:
        print "\n[ERROR] Please specify an application, e.g 'application:peoplefinder'"
        sys.exit(1)
    if env.config is None:
        # check if there is a deploy repo in a predefined location
        app_yaml = '../{0}-deploy/{0}.yaml'.format(env.application)
        if os.path.exists(app_yaml):
            env.config = app_yaml
        else:
            print "\n[ERROR] Please specify a config file, e.g 'config:/tmp/sample-application.yaml'"
            sys.exit(1)

    stack_name = get_stack_name()
    work_dir = os.path.join('..', '{0}-deploy'.format(env.application))
    # LOAD AWS CONFIG FROM ~/.config.yaml
    aws_config = AWSConfig(env.aws)

    project_config = ProjectConfig(env.config, env.environment)
    cfg = project_config.config

    local_salt_dir = os.path.join(
        work_dir,
        cfg.get('salt',{}).get(
            'local_salt_dir',
            'salt'),
        '.')
    local_pillar_dir = os.path.join(
        work_dir,
        cfg.get('salt',{}).get(
            'local_pillar_dir',
            'pillar'),
        '.')
    local_vendor_dir = os.path.join(
        work_dir,
        cfg.get('salt',{}).get(
            'local_vendor_dir',
            'vendor'),
        '.')

    remote_state_dir = cfg.get('salt',{}).get('remote_state_dir', '/srv/salt')
    remote_pillar_dir = cfg.get('salt',{}).get('remote_pillar_dir', '/srv/pillar')

    # if not os.path.exists(local_state_dir):
    #    shake(work_dir)

    master_ip = find_master()
    env.host_string = '{0}@{1}'.format(env.user, master_ip)
    sudo('mkdir -p {0}'.format(remote_state_dir))
    sudo('mkdir -p {0}'.format(remote_pillar_dir))
    upload_project(
        remote_dir=remote_state_dir,
        local_dir=os.path.join(
            local_vendor_dir,
            '_root',
            '.'),
        use_sudo=True)
    upload_project(
        remote_dir='/srv/',
        local_dir=os.path.join(
            local_vendor_dir,
            'formula-repos'),
        use_sudo=True)
    upload_project(
        remote_dir=remote_state_dir,
        local_dir=local_salt_dir,
        use_sudo=True)
    upload_project(
        remote_dir=remote_pillar_dir,
        local_dir=os.path.join(
            local_pillar_dir,
            env.environment,
            '.'),
        use_sudo=True)