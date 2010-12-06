NFS Test Helpers for Amazon EC2
===============================

This code runs various tests of the NFS protocol`s performance on Amazon EC2.

Getting Started
---------------

After you`ve downloaded the code, you`ll need to make a file called
"passwords.txt" containing a valid AWS Access Key ID on the first line, and
the associated Secret Access Key on the second line. Do not put anything else
in this file. These can be created through the Amazon AWS Control Panel. For
your safety, you may wish to `chmod 600` this file.

You will also need to create a keypair in the EC2 control panel called
"mypair" and place it in the "~/.ssh/" folder to communicate over ssh with the
test instances. The full path to the keypair should be "~/.ssh/mypair.pem".
You can modify this path in the source if you like.

Additional information and test results are available in the paper [TODO:
GoogleDoc link]

[This doc is still under construction.]
