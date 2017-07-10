'''

@author: wanzeyu

@contact: wan.zeyu@outlook.com

@file: server.py

@time: 2017/7/7 16:30

@desc:

'''
import os

from flask import Flask

app = Flask(__name__)


@app.route("/")
def hello():
    return "Hello 1-downtime %s World!" % os.environ.get('BLUEGREEN', 'bland')
