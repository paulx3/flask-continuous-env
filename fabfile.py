# -*- coding:utf-8 -*-
import os
from io import StringIO

from fabric.api import cd, put, local, sudo, task, run, require, prefix
from fabric.contrib.files import exists, append
from fabric.state import env
from fabric.utils import abort

from gitric.api import git_seed, git_reset, allow_dirty, force_push, swap_bluegreen

"""
env.hosts = ['HOST1', 'HOST2']  # cmd: fab -H HOST1,HOST2

# cmd: fab --set LIVE_SERVER_URL=example.com,NEXT_SERVER_URL=next.example.com
env.LIVE_SERVER_URL = 'example.com'
env.NEXT_SERVER_URL = 'next.example.com'
"""
env.user = 'root'
env.LIVE_SERVER_URL = 'example.com'
env.NEXT_SERVER_URL = 'next.example.com'


def nginx(action):
    # pty=False, here's why: http://www.fabfile.org/faq.html#init-scripts-don-t-work
    sudo('/etc/init.d/nginx %s' % action, pty=True)


def init_bluegreen():  # Taken from gitric.api, but modified so it uses linux-style path separators
    require('bluegreen_root', 'bluegreen_ports')
    env.green_path = env.bluegreen_root + '/green'
    env.blue_path = env.bluegreen_root + '/blue'
    env.next_path_abs = env.bluegreen_root + '/next'
    env.live_path_abs = env.bluegreen_root + '/live'
    run('mkdir -p %(bluegreen_root)s %(blue_path)s %(green_path)s '
        '%(blue_path)s/etc %(green_path)s/etc' % env)
    if not exists(env.live_path_abs):
        run('ln -s %(blue_path)s %(live_path_abs)s' % env)
    if not exists(env.next_path_abs):
        run('ln -s %(green_path)s %(next_path_abs)s' % env)
    env.next_path = run('readlink -f %(next_path_abs)s' % env)
    env.live_path = run('readlink -f %(live_path_abs)s' % env)
    env.virtualenv_path = env.next_path + '/env'
    env.pidfile = env.next_path + '/etc/app.pid'
    env.nginx_conf = env.next_path + '/etc/nginx.conf'
    env.color = os.path.basename(env.next_path)
    env.bluegreen_port = env.bluegreen_ports.get(env.color)


def install_requirements():
    """
    1. Install packages
    2. Update pip3
    3. Install virtualenv from pip
    """
    sudo('apt-get update')
    linux_packages = ['python3', 'python3-pip', 'nginx', 'git', 'curl']
    sudo('apt-get install -y ' + ' '.join(linux_packages))
    sudo('pip3 install --upgrade pip')
    sudo('pip3 install virtualenv')


def configure_nginx():
    """
    1. Remove default nginx config file
    2. Create new config file
    3. Setup new symbolic link
    4. Copy local config to remote config
    5. Make directories and empty config files for /live and /next config.
    6. Restart nginx
    """
    # End step 1
    nginx('start')
    sudo('rm -rf /etc/nginx/sites-enabled/default')
    sudo('touch /etc/nginx/sites-available/flask_site')
    sudo('ln -s /etc/nginx/sites-available/flask_site /etc/nginx/sites-enabled/flask_site')
    with cd('/etc/nginx/sites-available/'):
        with open(os.path.join('deploy', 'nginx.conf')) as f:  # Get local nginx config script
            put(StringIO(f.read() % env), './flask_site', use_sudo=True)
    # Step 5
    run('mkdir -p /home/%(user)s/blue-green/live/etc/' % env)
    run('mkdir -p /home/%(user)s/blue-green/next/etc/' % env)
    run('touch /home/%(user)s/blue-green/live/etc/nginx.conf' % env)
    run('touch /home/%(user)s/blue-green/next/etc/nginx.conf' % env)
    sudo('chown -R %(user)s:%(user)s /home/%(user)s/blue-green' % env)  # Shouldn't be necessary, but just in case.
    # End step 5
    nginx('restart')


@task
def prod():
    """
    生成路径信息
    :return:
    """
    if 'TRAVIS' in env and env.TRAVIS and env.TRAVIS_BRANCH != 'master':
        abort("Don't deploy from Travis unless it's from the master branch.")
    # 制定虚拟环境目录
    env.virtualenv_path = 'env'
    # 指定blue-green的路径
    env.bluegreen_root = '/home/%(user)s/blue-green' % env
    # 指定config的路径
    env.config_path = env.bluegreen_root + '/config'
    # 指定端口
    env.bluegreen_ports = {'blue': '8888', 'green': '8889'}
    # 初始化
    init_bluegreen()


def launch():
    # 根据pid文件删除进程
    run('kill $(cat %(pidfile)s) || true' % env)
    # 删除旧的虚拟环境
    run('rm -rf %(virtualenv_path)s' % env)  # Clear out old virtualenv for new one.
    # 创建新的虚拟环境
    run('virtualenv %(virtualenv_path)s' % env)
    # 将这条记录传到配置中
    put(StringIO('proxy_pass http://127.0.0.1:%(bluegreen_port)s/;' % env), env.nginx_conf)
    # run('cp %(repo_path)s/flask_site/config/config.yml %(config_path)s/config.yml' % env)
    with prefix('. %(virtualenv_path)s/bin/activate' % env), cd('%(repo_path)s' % env):
        run('pip3 install -r requirements.txt')
        run('pip3 install --ignore-installed gunicorn')
        # pty=False for last command since pseudo-terminals can't spawn daemons
        run('gunicorn -D -b 127.0.0.1:%(bluegreen_port)s -p %(pidfile)s '
            '--access-logfile access.log --error-logfile error.log app:app' % env, pty=True)


@task
def deploy_from_travis():
    # install_requirements()
    # configure_nginx()
    # 在next的目录下的repo文件夹
    env.repo_path = env.next_path + '/repo'
    # 删掉之前可能存在的文件夹
    run('rm -rf %(repo_path)s' % env)
    # 创建新的next下的repo文件夹
    run('mkdir %(repo_path)s' % env)
    # 将打包的文件命名为 deploy.tgz
    archive_name = 'deploy.tgz'
    # 调用pack函数进行打包，并返回本地打包文件的地址
    local_archive_path = pack(archive_name)  # Create tgz from local files
    # 将本地的打包文件传到服务器
    put(local_archive_path, env.repo_path)  # Upload to deployment server
    # 进入这个对应的文件夹并进行解压
    with cd(env.repo_path):
        run('tar xzf %s' % archive_name)  # Untar them to /repo
    # 启动部署
    launch()


def pack(archive_name):
    """
    Method to package a directory for deployment, returns local path of archive.
    """
    # 打包目录
    archive_path = 'tmp/' + archive_name
    # 删除tmp
    local('mkdir tmp')
    # 打包
    local('tar czf %s --exclude=tmp .' % archive_path)
    return archive_path


@task
def cutover():
    # blue_green切换
    swap_bluegreen()
    # reload nginx
    nginx('reload')
