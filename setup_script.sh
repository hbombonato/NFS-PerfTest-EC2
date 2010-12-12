#!/bin/bash
# Instance setup script (runs before instance reported as ready)
#
# Installs required software. Every instance gets the same thing. Wastes
# bandwidth relative to setting this up pre-deployment, but who cares.
#
# Note: this runs as root, so no need to mess with sudo
#
PACKAGES="iperf nfs-kernel-server nfs-common portmap traceroute"

apt-get -y update
apt-get -y install $PACKAGES
# No upgrades needed; we're not really worred about security

# Start iperf servers (TCP and UDP ports)
iperf -s &
iperf -su &

# Create and mount ramdisk
mkdir /tmp/ramdisk

# Help prevent files from accidentally getting created in the bare folder
# (which goes to EBS and costs money) rather than in the mounted ramdisk
chmod 000 /tmp/ramdisk

# This should have 777 permissions
mount -t tmpfs -o size=384M tmpfs /tmp/ramdisk/

# Create mount points for remote ramdisks
mkdir /mnt/remote_ramdisk_1

# Setup NFS server. No security since it is already setup at the AWS layer
EXPORT_STR="/tmp/ramdisk 10.0.0.0/8(rw,insecure,no_subtree_check,async,fsid=0)"
echo $EXPORT_STR >> /etc/exports

# NFSv4 will break without this
echo "NEED_IDMAPD=yes" > /etc/default/nfs-common

service nfs-kernel-server restart
