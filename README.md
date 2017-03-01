# ansible-dockercompose
ansible library module for controlling docker-compose services

Download dockercompose.py file and put them into your library directory (e.g. ./library in roles or relative to your workingdirectory).
Use it like:

    - dockercompose:
        location: /opt/myservices/docker-compose.yml
        command: 'pull'