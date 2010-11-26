#!/usr/bin/env python
# NFS EC2 Test Script
# By Brandon Thomson
# 
# Mostly designed to be used at the interactive iPython prompt
# 
from __future__ import print_function

from boto.ec2.connection import EC2Connection
import time
import subprocess

#AMAZON_LINUX_EBS = "ami-2272864b" # Required for micro instances

# 099720109477/ebs/ubuntu-images/ubuntu-lucid-10.04-amd64-server-20101020
UBUNTU_IMAGE = "ami-4a0df923"

DEBUG_MODE = False

# No upgrade here; we don't care about security
#
# Start the same services on every server, performance is not really likely to
# be affected
with open('setup_script.sh') as f:
  setup_script = f.read()

with open("passwords.txt") as f:
  key_id, key_secret, _ = f.read().split("\n")

conn = EC2Connection(key_id, key_secret)
if DEBUG_MODE:
  print("EC2 Connection opened")

def get_active_instances():
  instances = []

  for rsv in conn.get_all_instances():
    instances += rsv.instances

  if DEBUG_MODE:
    print("{0} total instances found.".format(len(instances)))

  instances = [inst for inst in instances if inst.state == u'running']

  if DEBUG_MODE:
    print("{0} active instances found.".format(len(instances)))

  return instances

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
        print("All instances ready.")
        return
      else:
        print("{0}/{1} instances ready.".format(status.count(u'running'),
                                                len(instances)))

    except boto.EC2ResponseError:
      print("EC2ResponseError")
    print("Will recheck in {0} seconds.".format(CHECK_DELAY))
    time.sleep(CHECK_DELAY)

ssh_args = [
  'ssh', '-i', '/home/bthomson/.ssh/mypair.pem', '-q',
  # Bypass MITM protection:
  '-o', 'UserKnownHostsFile=/dev/null', '-o', 'StrictHostKeyChecking=no',
]

def start_n_micro(n):
  if not 0 < n < 4: # Safety valve
    print("Warning: {0} is too many instances.".format(n))
    return
  resv = conn.run_instances(
    instance_type="t1.micro",
    key_name="mypair",
    min_count=n,
    max_count=n,
    image_id=UBUNTU_IMAGE,
    # It looks like all instances reserved at the same time will be in one
    # availability zone, but it doesn't hurt to sync them anyway
    placement="us-east-1b",
    user_data=setup_script
  )
  wait_all_active(resv.instances)
  return instances

def get_ssh_cmd_line(n):
  host_str = 'ubuntu@' + instances[n].public_dns_name
  return " ".join(ssh_args + [host_str])

def ssh_cmd(instance, args, block=False):
  host_str = 'ubuntu@' + instance.public_dns_name
  f_args = ssh_args + [host_str] + args
  if DEBUG_MODE:
    print(" ".join(f_args))
  if block:
    subprocess.check_call(f_args)
  else:
    return subprocess.Popen(f_args, stdout=subprocess.PIPE)

def mount_nfs_share(client, server):
  print("mounting nfs share on client")
  ssh_cmd(client, ['sudo', 'mount',
                   server.private_ip_address + ":/tmp/ramdisk",
                   "/mnt/remote_ramdisk_1"], block=True)

def unmount_nfs_share(client, server):
  print("unmounting nfs share on client")
  ssh_cmd(client, ['sudo', 'umount', '-l', '/mnt/remote_ramdisk_1'], block=True)

def unmount_ramdisk(machine):
  print("unmounting disk on server to clear any cache")
  umount_cmd = 'sudo umount -l /tmp/ramdisk'
  ssh_cmd(machine, umount_cmd.split(" "), block=True)

def mount_ramdisk(machine):
  print("remounting fresh ramdisk on server")
  mount_cmd = "sudo mount -t tmpfs -o size=384M tmpfs /tmp/ramdisk"
  ssh_cmd(machine, mount_cmd.split(" "), block=True)

def nfs(client_id, server_id):
  client, server = id_to_inst(client_id, server_id)
  print("nfs transfer from {0} -> {1}".format(client_id, server_id))

  mount_nfs_share(client, server)

  try:
    print("Transferring file")
    dd_cmd = "dd if=/dev/random of=/mnt/remote_ramdisk_1/test bs=1M count=1"
    ssh_cmd(client, dd_cmd.split(" "), block=True)
  finally:
    unmount_nfs_share(client, server)

  unmount_ramdisk(server)
  mount_ramdisk(server)

def id_to_inst(*args):
  """Turns id numbers into instances"""
  return (instances[x] for x in args)

def iperf_2(id1, id2):
  i1, i2 = id_to_inst(id1, id2)
  print("Iperf from {0} -> {1}".format(id1, id2))
  p1 = ssh_cmd(i1, ['iperf', '-c', i2.private_ip_address])

  stdout, _ = p1.communicate();
  if DEBUG_MODE:
    print(stdout)
  return stdout.split('\n')[6].split("    ")

def iperf_all():
  for i1 in instances:
    for i2 in instances:
      if i1 != i2:
        interval, size, speed = iperf2(i1, i2)

def term_all():
  for i in instances:
    i.terminate()
