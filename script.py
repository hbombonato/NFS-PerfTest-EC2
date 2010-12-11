#!/usr/bin/env python
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

def sizeof_fmt(num):
  for x in ['b','KB','MB','GB','TB']:
    if num < 1024.0:
      return "%3.1f%s" % (num, x)
    num /= 1024.0

with open('log_config.yaml') as f:
  config_dict = yaml.load(f.read())

logging.config.dictConfig(config_dict)

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

def random_fn():
  return ''.join(random.choice(string.letters + string.digits) for _ in range(10))

def get_ssh_cmd_line(n):
  host_str = 'ubuntu@' + instances[n].public_dns_name
  return " ".join(ssh_args + [host_str])

def ssh_cmd(instance, args, block=False):
  host_str = 'ubuntu@' + instance.public_dns_name
  f_args = ssh_args + [host_str] + args
  logging.debug("Raw cmd: " + " ".join(f_args))
  p = subprocess.Popen(f_args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
  if block:
    stdout, stderr = p.communicate()
    logging.debug("Raw output:\n" + stdout + stderr)
    if p.returncode:
      raise subprocess.CalledProcessError(p.returncode)
  else:
    return p

defaults = {
  # All nfs mounts will use these options by default. Changing this is an easy
  # way to change global mount options
  'nfs_opts': "rw,proto=tcp,soft,sync,vers=3",
}

def mount_nfs_share(client, server):
  logging.info("mounting nfs share with options {0}".format(defaults['nfs_opts']))
  try:
    ssh_cmd(client, ['sudo', 'mount', '-o', defaults['nfs_opts'],
                     server.private_ip_address + ":/tmp/ramdisk",
                     "/mnt/remote_ramdisk_1"], block=True)
  except subprocess.CalledProcessError as e:
    if e.returncode == 32: # already mounted
      return
    raise

def unmount_nfs_share(client, server):
  logging.debug("unmounting nfs share on client")
  ssh_cmd(client, ['sudo', 'umount', '-l', '/mnt/remote_ramdisk_1'], block=True)

def unmount_ramdisk(machine):
  logging.debug("unmounting disk on server to clear any cache")
  umount_cmd = 'sudo umount -l /tmp/ramdisk'
  ssh_cmd(machine, umount_cmd.split(" "), block=True)

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

def nfs_single(client_id, server_id, n_bytes):
  client, server = id_to_inst(client_id, server_id)
  logging.info("nfs {0} file transfer from {1} -> {2}".format(
    sizeof_fmt(n_bytes), client_id, server_id)
  )

  restart_nfs_service(server)
  mount_nfs_share(client, server)

  try:
    logging.debug("Transferring file")
    dd_cmd = ("dd if=/dev/urandom of=/mnt/remote_ramdisk_1/{0} "
              "bs={1} count=1".format(random_fn(), n_bytes))
    ssh_cmd(client, dd_cmd.split(" "), block=True)
  finally:
    unmount_nfs_share(client, server)
    unmount_ramdisk(server)
    mount_ramdisk(server)

def nfs_multi(client_id, server_id, count, n_bytes):
  client, server = id_to_inst(client_id, server_id)
  log_str = ("nfs multi transfer: create {0} {1} files on {2} (server) from "
             "{3} (client)")
  logging.info(log_str.format(count, sizeof_fmt(n_bytes), server_id, client_id))

  restart_nfs_service(server)
  mount_nfs_share(client, server)

  try:
    logging.debug("Transferring file")
    script = ('"for i in {1..%d}; do dd if=/dev/urandom '
              'of=/mnt/remote_ramdisk_1/file_$i bs=%d count=1; done"' %
              (count, n_bytes))
    args = ['/usr/bin/time', '-f', "%e", 'bash', '-c', script]
    p = ssh_cmd(client, args, block=False)
    stdout, stderr = p.communicate()
  finally:
    unmount_nfs_share(client, server)
    unmount_ramdisk(server)
    mount_ramdisk(server)

  return float(stderr.split("\n")[-2])

def all_tests():
  logging.info("Executing all tests")
  iperf_all()
  nfs_single(0,1,1024)
  nfs_multi(0,1,1024,1024)

def get_date_time():
  return str(datetime.datetime.now()).split(" ")

def get_csv_writer(f):
  return csv.writer(f, delimiter=' ', quotechar='|', quoting=csv.QUOTE_MINIMAL)

def id_to_inst(*args):
  """Turns id numbers into instances"""
  if type(args[0]) == type(0):
    return (instances[x] for x in args)
  return args

def iperf_2(client_id, server_id):
  client, server = id_to_inst(client_id, server_id)
  logging.info("Iperf from {0} -> {1}".format(client_id, server_id))

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
      [date, time, client_id, server_id, client.id, server.id, tcp_interval, tcp_size, tcp_speed,
       udp_interval, udp_size, udp_speed, jitter, num_lost, datagrams_sent,
       num_out_of_order]
    )

def iperf_all_sequential():
  for i1 in instances:
    for i2 in instances:
      if i1 != i2:
        iperf_2(i1, i2)

def term_all():
  for i in instances:
    i.terminate()
