#!/bin/env python2.6

"""
Django uWSGI service controller. Handles the start, stop, reload and restarting of a given Django 
application. 

Copyright (c) 2010 Bryan Pieper http://www.thepiepers.net/

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.

For more information on uWSGI, see http://projects.unbit.it/uwsgi/

Use the wsgi python file to run the wsgi-compliant application. The default file is [appname]/django_wsgi.py.
Starts the uWSGI command using unix sockets named by the app name. The pid file and socket are stored
in the temp workspace.

Use the 'reload' argument to restart gracefully. Requires uWSGI 0.9.5.

For more help, ./django-wsgi-svc.py --help
"""
from optparse import OptionParser

import os
import sys
import subprocess
import signal
import time

# requires python2.6
import multiprocessing
cpu_count = multiprocessing.cpu_count()

# command tied to functions in main
cmd_list = ('start', 'stop', 'restart', 'status', 'stats', 'reload')


def poll(pid):
    """
    Checks to see if the given pid is running.
    """
    try:
        # does nothing if process is running
        os.kill(pid, 0)
    except OSError:
        return False
    else:       
        return True if pid else False


def main():
    parser = OptionParser(usage="[appname] [{0}]".format('|'.join(cmd_list)))
    parser.add_option('--workers', type='int', dest='workers', default=cpu_count, \
                      help="Number of worker processes, defaults to number of CPUs/cores")
    parser.add_option('--queue', type='int', dest='queue', default=512, \
                      help="The connect listen queue")
    parser.add_option('--socket_timeout', type='int', dest='socket_timeout', default=20, \
                      help="Socket timeout")
    parser.add_option('--process_timeout', type='int', dest='process_timeout', default=20, \
                      help="Process timeout")
    parser.add_option('-d', '--debug', action='store_true', dest='debug', \
                      help="Enable debug mode. This will add request data to log file")
    parser.add_option('--max_requests', type='int', dest='max_requests', default=4000, \
                      help="Set the number of max requests per process")
    parser.add_option('--buffer', type='int', dest='buffer', default=8096, \
                      help="Request buffer")
    parser.add_option('--webroot', dest='webroot', action='store', default='webroot', type='string', \
                      help="Webroot directory name, default is webroot")
    parser.add_option('--base', action='store', default=os.environ['HOME'], type='string', \
                      help="The base directory for the webapp, defaults to env HOME_DIR")
    parser.add_option('--tmp_dir', action='store', default=os.path.join(os.environ['HOME'], 'tmp'), \
                      type='string', dest='tmp_dir', help="The temp directory workspace, defaults to HOME_DIR/tmp")
    parser.add_option('--wsgi_py', dest='wsgi_py', default='django_wsgi', type='string', \
                      help="wsgi application module, defaults to django_wsgi")
    parser.add_option('--python_path', dest='python_path', default='', type='string', \
                      help="Additional python path to include. Delimited by : ")
    parser.add_option('--uwsgi_cmd', dest='uwsgi_cmd', default='/usr/bin/uwsgi', type='string', \
                      help="uWSGI binary exec name")
    parser.add_option('--foreground', dest='foreground', action='store_true', \
                      help="Run the service in the foreground. Skips the log file.")

    options, args = parser.parse_args()
   
    base_dir = options.base
    webroot_dir = options.webroot
    app_base = os.path.join(base_dir, webroot_dir)

    if not os.path.exists(app_base):
        parser.error("app base {0} does not exist".format(app_base))
        sys.exit(1)

    if len(args) != 2:
        if len(args):
            parser.error("Incorrect number of app arguments")
        parser.print_usage()
        sys.exit(1)

    app_name, app_cmd = args
    app_cmd = app_cmd.lower()

    # check cmd list
    if app_cmd not in cmd_list:
        parser.error("Incorrect app command {0}".format(app_cmd))
        parser.print_usage()
        sys.exit(1)

    if not os.path.exists(os.path.join(app_base, app_name)):
        parser.error("app name {0} does not exist".format(app_name))
        sys.exit(1)

    tmp_dir = options.tmp_dir
    if not os.path.exists(tmp_dir):
        os.makedirs(tmp_dir)
        os.chmod(tmp_dir, 0755)
        print "Created tmp dir {0}".format(tmp_dir)

    pid_file = os.path.join(tmp_dir, '{name}_wsgi.pid'.format(name=app_name))
    socket_file = os.path.join(tmp_dir, '{name}_wsgi.sock'.format(name=app_name))
    log_file = os.path.join(tmp_dir, '{name}_wsgi.log'.format(name=app_name))

    pid_num = 0
    if os.path.exists(pid_file):
        try:
            pid_num = int(open(pid_file).read().strip())
        except:
            os.remove(pid_file)

    pid_running = poll(pid_num)
    if not pid_running:
        # toss workspace if not running
        if os.path.exists(pid_file):
            os.remove(pid_file)
        if os.path.exists(socket_file):
            os.remove(socket_file)

    # make the app the current directory
    os.chdir(os.path.join(app_base, app_name))

    def status():
        if pid_running:
            print "{name} PID {pid} is running".format(name=app_name, pid=pid_num)
        else:
            print "{name} is not running".format(name=app_name)
        return pid_running

    def start():
        """
        Starts the uWSGI daemon.
        """
        if pid_running:
            print "{name} is already running".format(name=app_name)
            return False
        else:
            print "Starting {name}...".format(name=app_name)
            py_path = os.path.join(app_base, app_name)
            wsgi_exec = options.uwsgi_cmd

            # see http://projects.unbit.it/uwsgi/wiki/Doc for more details
            wsgi_cmd = [wsgi_exec, '-C', '-M', 
                        '-s', socket_file, 
                        '-p', options.workers, 
                        '-R', options.max_requests, 
                        '-w', options.wsgi_py, 
                        '--pidfile', pid_file, 
                        '-b', options.buffer, 
                        '-l', options.queue, 
                        # include app base in python path
                        '--pythonpath', py_path,
                        '-z', options.socket_timeout,
                        '-t', options.process_timeout,
                        # required for reload command to work uWSGI 0.9.5
                        '--binary-path', wsgi_exec]

            if not options.debug:
                wsgi_cmd.append('-L')
           
            if options.debug:
                print "uWSGI cmd: {0}".format(" ".join(map(str, wsgi_cmd)))
                print "Log file: {0}".format(log_file)

            if not options.foreground:
                wsgi_cmd.extend(['-d', log_file])

            if options.python_path:
                # apply python paths, separated by ":"
                for p in options.python_path.split(':'):
                    wsgi_cmd.extend(['--pythonpath', p])
            
            # run without waiting 
            pid = subprocess.Popen(map(str, wsgi_cmd)).pid  
            print "Running {name} with PID {pid}".format(name=app_name, pid=pid)
            return True

    def stop():
        """
        Stops the uWSGI daemon.
        """
        if not pid_running:
            print "{name} is not running".format(name=app_name)
            return False
        print "Stopping {name}...".format(name=app_name)
        os.kill(pid_num, signal.SIGINT)
        # poll the process until fully stopped
        while True:
            if poll(pid_num):
                time.sleep(.1)
            else:
                break
        print "Stopped" 
        if os.path.exists(pid_file):
            os.remove(pid_file)
        if os.path.exists(socket_file):
            os.remove(socket_file)
        return True

    def restart():
        """
        Restarts the daemon
        """
        if not pid_running:
            print "{name} is not running".format(name=app_name)
            return False
        os.kill(pid_num, signal.SIGTERM)
        print "Sent restart signal to PID {0}".format(pid_num)
        return True

    def stats():
        """
        Prints the current stats to the log
        """
        if not pid_running:
            print "{name} is not running".format(name=app_name)
            return False
        os.kill(pid_num, signal.SIGUSR1)
        print "{name} stats printed to {file}".format(name=app_name, file=log_file)
        return True

    def reload():
        """
        Gracefully reload the daemon.
        """
        if not pid_running:
            print "{name} is not running".format(name=app_name)
            return False
        os.kill(pid_num, signal.SIGHUP)
        print "Sent reload signal to PID {0}".format(pid_num)
        return True

    # run command
    exit_val = eval(app_cmd)()    
    sys.exit(0 if exit_val else 1) 
        

if __name__ == '__main__':
    main()

