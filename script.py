#!/usr/bin/env python
# vim: set tw=74:
#
# NFS EC2 Test Script
# By Brandon Thomson

# Requires Python 2.7 or newer
# Designed to support use at the interactive iPython prompt: use %run script
#
from __future__ import print_function

from boto.ec2.connection import EC2Connection
import time
import subprocess
import logging, logging.config
import yaml
import random
import string
import csv
import datetime

##########################################################################
## Utility functions: utility stuff and logging setup
##########################################################################

def sizeof_fmt(num):
  for x in ['b','KB','MB','GB','TB']:
    if num < 1024.0:
      return "%3.1f%s" % (num, x)
    num /= 1024.0

with open('log_config.yaml') as f:
  config_dict = yaml.load(f.read())

logging.config.dictConfig(config_dict)

def random_fn():
  return ''.join(random.choice(string.letters + string.digits) for _ in range(10))

def get_ssh_cmd_line(n):
  host_str = 'ubuntu@' + instances[n].public_dns_name
  return " ".join(ssh_args + [host_str])

class ProcException(Exception): pass

ssh_args = [
  'ssh',

  # Specify AWS keypair
  '-i', '/home/bthomson/.ssh/mypair.pem',

  # No useless output
  '-q',

  # Bypass MITM protection:
  '-o', 'UserKnownHostsFile=/dev/null', '-o', 'StrictHostKeyChecking=no',

  # Enable compression to save on bandwidth charges
  '-C',
]

def ssh_cmd(instance, args, block=False):
  host_str = 'ubuntu@' + instance.public_dns_name
  f_args = ssh_args + [host_str] + args
  logging.debug("Raw cmd: " + " ".join(f_args))
  p = subprocess.Popen(f_args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
  if block:
    stdout, stderr = p.communicate()
    logging.debug("Raw output:\n" + stdout + stderr)
    if p.returncode:
      raise ProcException(p.returncode)
    return stdout, stderr
  else:
    return p

def get_date_time():
  return str(datetime.datetime.now()).split(" ")

def get_csv_writer(f):
  return csv.writer(f, delimiter=' ', quotechar='|', quoting=csv.QUOTE_MINIMAL)

def id_to_inst(*args):
  """Turns id numbers into instances"""
  if type(args[0]) == type(0):
    return (instances[x] for x in args)
  return args

##########################################################################
## EC2 Setup functions: For assigning and starting machines
##########################################################################

#AMAZON_LINUX_EBS = "ami-2272864b" # Required for micro instances

# 099720109477/ebs/ubuntu-images/ubuntu-lucid-10.04-amd64-server-20101020
UBUNTU_IMAGE = "ami-4a0df923"

# Start the same services on every server, performance is not really likely to
# be affected, it's just a few services
with open('setup_script.sh') as f:
  setup_script = f.read()

with open("passwords.txt") as f:
  key_id, key_secret, _ = f.read().split("\n")

conn = EC2Connection(key_id, key_secret)
logging.debug("New EC2 Connection opened")

def get_active_instances():
  instances = []

  for rsv in conn.get_all_instances():
    instances += rsv.instances

  logging.debug("{0} total instances found.".format(len(instances)))

  instances = [inst for inst in instances if inst.state == u'running']

  logging.debug("{0} active instances found.".format(len(instances)))

  return instances

# "instances" tracks ALL instances we are currently paying for... use this to
# do iperf between all pairs of machines, etc.
instances = get_active_instances()

# Add an idx parameter to all instances (used as a simple integer id)
for idx, inst in enumerate(instances):
  inst.idx = idx

def start_one_micro():
  resv = conn.run_instances(instance_type="t1.micro",
                            key_name="mypair",
                            image_id=UBUNTU_IMAGE)

  inst = resv.instances[0]
  return inst

def wait_all_active(instances):
  CHECK_DELAY = 10
  ready = 0
  while 1:
    try:
      status = [inst.update() for inst in instances]
      if all(x == u'running' for x in status):
        logging.info("All instances ready.")
        return
      else:
        logging.info("{0}/{1} instances ready.".format(status.count(u'running'),
                                                len(instances)))

    except boto.EC2ResponseError:
      logging.error("EC2ResponseError")
    logging.info("Will recheck in {0} seconds.".format(CHECK_DELAY))
    time.sleep(CHECK_DELAY)

def start_n_micro(n):
  if not 0 < n < 4: # Safety valve
    logging.error("Warning: {0} is too many instances.".format(n))
    return
  resv = conn.run_instances(
    instance_type="t1.micro",
    key_name="mypair",
    min_count=n,
    max_count=n,
    image_id=UBUNTU_IMAGE,
    # It looks like all instances reserved at the same time will be in one
    # availability zone, but it doesn't hurt to sync them up anyway
    placement="us-east-1b",
    user_data=setup_script
  )
  wait_all_active(resv.instances)
  return instances

def term_all():
  for i in instances:
    i.terminate()

##########################################################################
## Test functions: Stuff related to running tests
##########################################################################

# This is a global so we don't have to pass it around everywhere... it
# would make running tests from the console more annoying if we passed it
# around too
#
# the vary_nfs_opts() function modifies these values as appropriate, then
# they are read by each NFS test function
nfs_opts = {
  'opt_str': 'rw,proto=tcp,soft,sync,vers=3,rsize=32768,wsize=32768',
  'version': 'v3',
  'rsize': '32768',
  'wsize': '32768',
  'proto': 'tcp',
}

def all_tests():
  """This is the highest-level function and should run all tests in order,
  assuming there are no problems executing anything."""
  logging.info("Executing all tests")

  network_test_all_sequential()
  nfs_single(0,1,1024)
  nfs_multi(0,1,1024,1024)

def vary_nfs_opts(f, *args):
  #for rsize in [2**x for x in range(5,20)]
  for proto in ('tcp', 'udp'):
    for version in ('v3', 'v4'):
      if proto == 'tcp':
        wsizes = [2**(2*x+1) for x in range(4,10)]
      else:
        wsizes = [512, 2048] # 8192 is an error
      for wsize in wsizes:
        fmt_s = 'rw,proto={0},soft,sync{1},rsize=32768,wsize={2}'
        vers = ',vers=3' if version == 'v3' else ''
        nfs_opts['opt_str'] = fmt_s.format(proto, vers, wsize)

        nfs_opts['version'] = version
        nfs_opts['rsize'] = '32768'
        nfs_opts['wsize'] = wsize
        nfs_opts['proto'] = proto
        f(*args)

def vary_nfs_multi(client_id, server_id):
  client, server = id_to_inst(client_id, server_id)

  try:
    unmount_nfs_share(client, server)
    unmount_nfs_share(client, server)
    unmount_nfs_share(client, server)
  except ProcException:
    pass

  restart_nfs_service(server)

  for n_bytes in [1024, 65536, 524288, 1048576]:
    for count in [10, 50, 250]:
      vary_nfs_opts(nfs_multi, client_id, server_id, count, n_bytes)

def vary_nfs_single(client_id, server_id):
  client, server = id_to_inst(client_id, server_id)

  try:
    unmount_nfs_share(client, server)
    unmount_nfs_share(client, server)
    unmount_nfs_share(client, server)
  except ProcException:
    pass

  try:
    mount_ramdisk(server)
  except ProcException:
    pass

  restart_nfs_service(server)

  for n_bytes in [1024, 65536, 524288, 1048576, 10485760, 78643200, 262144000]:
    vary_nfs_opts(nfs_single, client_id, server_id, n_bytes)

def mount_nfs_share(client, server):
  logging.info("mounting nfs share with options {0}".format(nfs_opts['opt_str']))

  opts = ['sudo', 'mount']
  if nfs_opts['version'] == 'v4':
    opts += ['-t', 'nfs4']
    mnt_path = ":/"
  else:
    mnt_path = ":/tmp/ramdisk"

  opts += ['-o', nfs_opts['opt_str'], server.private_ip_address +
           mnt_path, "/mnt/remote_ramdisk_1"]

  try:
    ssh_cmd(client, opts, block=True)
  except ProcException as e:
    # This seems to be wrong
    if e.args[0] == 32: # already mounted
      return
    raise

def unmount_nfs_share(client, server):
  logging.debug("unmounting nfs share on client")
  ssh_cmd(client, ['sudo', 'umount', '-l', '/mnt/remote_ramdisk_1'], block=True)

def unmount_ramdisk(machine):
  logging.debug("unmounting disk on server to clear any cache")
  umount_cmd = 'sudo umount -l /tmp/ramdisk'
  try:
    ssh_cmd(machine, umount_cmd.split(" "), block=True)
  except ProcException:
    # Only reason this should fail is if it's already unmounted
    logging.warning("umount failed")

def mount_ramdisk(machine):
  logging.debug("remounting fresh ramdisk on server")
  mount_cmd = "sudo mount -t tmpfs -o size=384M tmpfs /tmp/ramdisk"
  ssh_cmd(machine, mount_cmd.split(" "), block=True)

def restart_nfs_service(machine):
  """Technically this should not be necessary, but it seems like there are
  some bugs in the latest NFS server on ubuntu"""
  logging.debug("restarting nfs kernel service")
  mount_cmd = "sudo service nfs-kernel-server restart"
  ssh_cmd(machine, mount_cmd.split(" "), block=True)

def nfs_multi_client_single_file(client_ids, server_id, n_bytes):
  # TODO: make this threadded, write to different CSV file
  for client in client_ids:
    nfs_single(client_id, server_id, n_bytes)

def nfs_multi_client_multi_file(client_ids, server_id, count, n_bytes):
  # TODO: make this threadded, write to different CSV file
  for client in client_ids:
    nfs_multi(client_id, server_id, count, n_bytes)

def nfs_single(client_id, server_id, n_bytes):
  client, server = id_to_inst(client_id, server_id)
  logging.info("nfs {0} file transfer from {1} -> {2}".format(
    sizeof_fmt(n_bytes), client_id, server_id)
  )

  #restart_nfs_service(server)
  mount_nfs_share(client, server)

  try:
    logging.debug("Transferring file")
    dd_cmd = ("dd if=/dev/urandom of=/mnt/remote_ramdisk_1/{0} "
              "bs={1} count=1".format(random_fn(), n_bytes))
    _, stderr = ssh_cmd(client, dd_cmd.split(" "), block=True)
  except ProcException:
    logging.warning("dd failure")
    duration, speed = '-', '-'
  else:
    _, duration, speed = stderr.split("\n")[2].strip().split(", ")
    speed = speed.replace(' ', '')
    duration = duration.split(" ")[0]
  finally:
    unmount_nfs_share(client, server)
    unmount_ramdisk(server)
    mount_ramdisk(server)

  date, time = get_date_time()

  with open('nfs_single.csv', 'ab') as f:
    csv_writer = get_csv_writer(f)
    csv_writer.writerow(
      [date, time, client_id, server_id, client.id, server.id, n_bytes,
       duration, speed, nfs_opts['version'], nfs_opts['rsize'],
       nfs_opts['wsize'], nfs_opts['proto']]
    )

def nfs_multi(client_id, server_id, count, n_bytes):
  client, server = id_to_inst(client_id, server_id)
  log_str = ("nfs multi transfer: create {0} {1} files on {2} (server) from "
             "{3} (client)")
  logging.info(log_str.format(count, sizeof_fmt(n_bytes), server_id, client_id))

  #restart_nfs_service(server)
  mount_nfs_share(client, server)

  try:
    logging.debug("Transferring file")
    script = ('"for i in {1..%d}; do dd if=/dev/urandom '
              'of=/mnt/remote_ramdisk_1/file_$i bs=%d count=1; done"' %
              (count, n_bytes))
    args = ['/usr/bin/time', '-f', "%e", 'bash', '-c', script]
    _, stderr = ssh_cmd(client, args, block=True)
  finally:
    unmount_nfs_share(client, server)
    unmount_ramdisk(server)
    mount_ramdisk(server)

  duration = float(stderr.split("\n")[-2])
  date, time = get_date_time()

  with open('nfs_multi.csv', 'ab') as f:
    csv_writer = get_csv_writer(f)
    csv_writer.writerow(
      [date, time, client_id, server_id, client.id, server.id, n_bytes,
       count, duration, nfs_opts['version'], nfs_opts['rsize'],
       nfs_opts['wsize'], nfs_opts['proto']]
    )

def log_2_test(test_name, id1, id2):
  logging.info("{0} from {1} -> {2}".format(test_name, id1, id2))

def tracert(src_id, target_id):
  log_2_test("tracert", src_id, target_id)
  src, target = id_to_inst(src_id, target_id)

  stdout, _ = ssh_cmd(src, ['traceroute', target.private_ip_address],
                      block=True)

  num_hops = len(stdout.split("\n")) - 2

  date, time = get_date_time()

  with open('tracert.csv', 'ab') as f:
    csv_writer = get_csv_writer(f)
    csv_writer.writerow(
      [date, time, src_id, target_id, src.id, target.id, num_hops]
    )

def ping(src_id, target_id):
  log_2_test("ping", src_id, target_id)
  src, target = id_to_inst(src_id, target_id)
  stdout, _ = ssh_cmd(src, ['ping', '-c', '2', target.private_ip_address],
                      block=True)

  ping_time = stdout.split("\n")[2].strip().split("=")[3].split(" ")[0]

  date, time = get_date_time()

  with open('ping.csv', 'ab') as f:
    csv_writer = get_csv_writer(f)
    csv_writer.writerow(
      [date, time, src_id, target_id, src.id, target.id, ping_time]
    )

def iperf_2(client_id, server_id):
  client, server = id_to_inst(client_id, server_id)
  log_2_test("iperf", client_id, server_id)

  p1 = ssh_cmd(client, ['iperf', '-c', server.private_ip_address, '--reportstyle=C'])
  stdout, stderr = p1.communicate();
  if stderr:
    logging.error("Raw stderr from iperf TCP:\n" + stderr)
    raise Exception()

  logging.debug("Raw output from iperf TCP:\n" + stdout)
  s = stdout.strip().split(',')
  tcp_interval, tcp_size, tcp_speed = s[6], s[7], s[8]

  p1 = ssh_cmd(client, ['iperf', '-c', server.private_ip_address, '-u', '-b', '10m',
                        '--reportstyle=C'])
  stdout, stderr = p1.communicate();
  if stderr:
    logging.error("Raw stderr from iperf UDP:\n" + stderr)
    raise Exception()

  logging.debug("Raw output from iperf UDP:\n" + stdout)
  lines = stdout.split('\n')
  o = lines[0].replace('\n','').split(',')
  t = lines[1].replace('\n','').split(',')

  udp_interval, udp_size, udp_speed = o[6], o[7], o[8]
  datagrams_sent, jitter, num_lost = t[11], t[9], t[10]
  num_out_of_order = t[13]

  date, time = get_date_time()

  with open('iperf.csv', 'ab') as f:
    csv_writer = get_csv_writer(f)
    csv_writer.writerow(
      [date, time, client_id, server_id, client.id, server.id, tcp_interval,
       tcp_size, tcp_speed, udp_interval, udp_size, udp_speed, jitter,
       num_lost, datagrams_sent, num_out_of_order]
    )

def network_test_all_sequential():
  """Runs network tests between all pairs of instances"""
  for i1 in instances:
    for i2 in instances:
      if i1 != i2:
        iperf_2(i1, i2)
        ping(i1, i2)
        tracert(i1, i2)
