#!/usr/bin/python
# 
# Copyright (c) 2017 Ernest Czerwonka (@czerwe)
# 
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
# 
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
# 
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

import json
import yaml
import os
import sys

DOCUMENTATION = '''
---
module: localpuppet
short_description: Runs puppet
description:
  - Runs I(puppet) agent or apply in a reliable manner
version_added: "2.0"
options:
  location:
    description:
      - Absolute path to the docker-compose.yaml file.
    required: true
    default: None
  services:
    description:
      - List of services to be controlled. Leave empty to controll all services inside the yaml file.
    required: false
    default: []
  command:
    description:
      - state of service
    required: false
    default: up
    choices: ['up', 'down', 'start', 'stop', 'pause', 'unpause', 'pull', 'create', 'build']
  removevolumes:
    description:
      - if command == down the down command removes also all volumes
    required: false
    default: []
  ignoredependencys:
    description:
      - If an service mentioned in the 'service' parameter have dependencys to other services they will by default added to the service list.
    required: false
    default: []
requirements: [ docker-compose ]
author: "Ernest Czerwonka (@czerwe)"
'''

EXAMPLES = '''
# Startup all services defined in /opt/myservices/docker-compose.yml
- dockercompose: location=/opt/myservices/docker-compose.yml

# Pull images only
- dockercompose:
    location: /opt/myservices/docker-compose.yml
    command: 'pull'


# delete services without deleting all volumes
- dockercompose:
    location: /opt/myservices/docker-compose.yml
    command: 'down'
    removevolumes: false

'''

def main():
    module = AnsibleModule(
        argument_spec = dict(
            location = dict(required=True, default=None),
            services = dict(required=False, default=[], type='list'),
            command = dict(required=False, default='up', choices=['up', \
                    'down', \
                    'start', \
                    'stop', \
                    'pause', \
                    'unpause', \
                    'pull', \
                    'create', 'build']),
            ignoredependencys = dict(required=False, default=False, type='bool'),
            removevolumes = dict(required=False, default=True, type='bool'),
        )
    )

    p = module.params

    COMPOSE_CMD = module.get_bin_path("docker-compose")

    if not COMPOSE_CMD:
        # if sudo is used the PATH variable is resetted. Try to find the docker-compose by myself
        alternative_cmd = os.path.join('/usr/local/bin', 'docker-compose')
        if os.path.exists(alternative_cmd):
            COMPOSE_CMD = alternative_cmd
        else:
            module.fail_json(msg="docker-compose binary not found")

    if not os.path.exists(p['location']) or not os.path.isfile(p['location']):
        module.fail_json( msg="Location does not exists. Enter full path and compose filename.")


    if p['command'] == 'down' and len(p['services']) >= 1:
        module.fail_json( msg="You cannot specify the command as down and a number of services")


    try:
        yml_raw = open(p['location']).read()
        info = yaml.load(yml_raw)
    except:
        module.fail_json( msg="YAML cannot be decoded.")

    os.chdir(os.path.dirname(p['location']))


    # evaluating the dependecys for the selected services. This is only a forward resolve for up and starting
    depedency_services = []
    if not p['ignoredependencys']:
        all_services = info.get('services', [])

        for service in p['services']:
            current_service = all_services.get(service, {})
            depedency_services = list(set(depedency_services + current_service.get('depends_on',[]))) 


    # Setup commandline
    command_list = [COMPOSE_CMD]

    if p['command'] == 'up':
        command_list.append('up -d')
    else:
        command_list.append(p['command'])

    if p['command'] in ['stop', 'down']:
        unpause_first_command_list = [COMPOSE_CMD, 'unpause']
        unpause_first_command_list.append(' '.join(list(set(depedency_services + p['services']))))
        unpause_first_command_str = ' '.join(unpause_first_command_list)
        rc, stdout, stderr = module.run_command(unpause_first_command_str)
    
    # down does not allow to set specific services to be down
    if not p['command'] == 'down':
        command_list.append(' '.join(list(set(depedency_services + p['services']))))
    else:
        if p['removevolumes']:
            command_list.append('-v')

    stdout = ''
    stderr = ''

    command_str = ' '.join(command_list)

    rc, stdout, stderr = module.run_command(command_str)


    stderr_lines = stderr.splitlines()
    stdout_lines = stdout.splitlines()

    changed, failed = eval_change(p['command'], stderr_lines, stdout_lines)

    ms = [len(stderr_lines), changed, failed]

    # print failed

    if failed:
        module.fail_json(msg="Build Failed.", stdout=stdout, stderr=stderr)
    else:
        module.exit_json(rc=rc, changed=changed, msg=ms, stdout=stdout, stderr=stderr)


def eval_change(action, stderr_lines, stdout_lines):

    changeVal = False
    failVal = False

    if action == 'up':
        valid_lines = [ln for ln in stderr_lines if ln.startswith('Starting') or ln.startswith('Creating')]
        for valid_line in valid_lines:
            if not valid_line.endswith('up-to-date'):
                changeVal = True

    if action == 'pull':
        for stdout_line in stdout_lines:
            if 'Status: Downloaded newer image' in stdout_line:
                changeVal = True

    if action == 'build':
        for stderr_line in stderr_lines:
            if 'failed to build' in stderr_line:
                failVal = True

    if action == 'stop' and len(stderr_lines) > 0:
        changeVal = True

    if action == 'create' and len(stderr_lines) > 0:
        changeVal = True

    # docker-compose start does not print out usable output to determine if the system has changes
    if action == 'start':
        # valid_lines = [ln for ln in stderr_lines if ln.startswith('Starting')]
        changeVal = False

    if action == 'pause':
        service_state = service_state_allign(stderr_lines, prefix='Pausing')
        for service in service_state:
            if not service_state[service].endswith('is already paused'):
                changeVal = True

    if action == 'unpause':
        service_state = service_state_allign(stderr_lines, prefix='Unpausing')
        for service in service_state:
            if not service_state[service].endswith('is not paused'):
                changeVal = True

    return [changeVal, failVal]
    


def service_state_allign(lines, prefix):
    
    pausing_lines = [ln for ln in lines if ln.startswith(prefix) and not ln.strip().endswith('...')]
    error_lines = [ln for ln in lines if ln.startswith('ERROR')]

    error_services = [paused_service.split()[1] for paused_service in pausing_lines if paused_service.endswith('error')]
    done_services = [paused_service.split()[1] for paused_service in pausing_lines if paused_service.endswith('done')]

    service_state = dict()

    for error_service in error_services:
        for error_line in error_lines:
            if error_service in error_line:
                service_state[error_service] = error_line


    for done_service in done_services:
        service_state[done_service] = 'done'

    return service_state




from ansible.module_utils.basic import *
main()