import tensorflow as tf
import numpy as np

# preprocessing functions

# for global contrast normalization
def gcn(x, s=1, l=0, e=10**(-8)):
#     transpose x(NHWC)->x(CHWN)
    [N, H, W, C] = x.shape
    x = x.transpose([3,1,2,0])
    mean = (np.ones([H, W, N]) * (np.ones([W, N]) * np.mean(a=x, axis=(0,1,2))))
    div = np.sqrt(l + np.sum(a=np.square(x - mean), axis=(0,1,2))/(C * W * H))
#     implement max(e, xi) elementwise in tensor
    div[div < e] = e
    ret = (x - mean) / (np.ones([H, W, N]) * (np.ones([W, N]) * div))
#     transpose back to (NHWC)
    return ret.transpose([3,1,2,0])

# zca whitening
def zca(x, e=0.1):
    x_white = np.reshape(x, (-1, x.shape[0]), 'C')
    [U, S, V] = np.linalg.svd(np.dot(x_white, x_white.transpose()) / x_white.shape[0])
    x_white =np.dot(U, np.dot(np.diag(1 / np.sqrt(S + e)), np.dot(U.transpose(), x_white)))
    return np.reshape(x_white, x.shape, 'C')



# Create some wrappers for simplicity
def conv2d(x, W, b, strides=1):
    x = tf.nn.conv2d(x, W, strides=[1, strides, strides, 1], padding = 'SAME')
    x = tf.nn.bias_add(x, b)
    return tf.nn.relu(x)

# extract patches from feature maps
# input shape N, H, W, C
# output shape N, H, W, K, C
def extract_patches(x, padding, ksize=2, stride=2):
    temp = tf.extract_image_patches(images=x, ksizes=[1, ksize, ksize, 1], strides=[1, stride, stride, 1], rates=[1,1,1,1], padding=padding)
    [N, H, W, C] = temp.get_shape().as_list()
    C = x.get_shape().as_list()[-1]
#     reshape to N,H,W,K,C
    temp = tf.reshape(temp, [-1, H, W, ksize*ksize, C])
    return temp


# compute the frequency of element in each patch
# input extracted patches tensor in shape N, H, W, K, C
# output frequency tensor in shape N, H, W, K, C
def majority_frequency(temp):
    [N, H, W, K, C] = temp.get_shape().as_list()
    print([N, H, W, K, C])
    temp = tf.to_int32(tf.round(temp))
#     build one hot vector
    temp = tf.transpose(temp, [0,1,2,4,3])
    one_hot = tf.one_hot(indices=temp, depth=tf.reduce_max(temp) + 1, dtype=tf.float32)
#     the dimension is bathch, row, col, lay, one hot
#     the order tensorflow takes, when doiong transpose, it will from the most right to most left
    one_hot = tf.reduce_sum(one_hot, axis=4)
    temp = tf.transpose(temp, [0, 3, 1, 2, 4])
    temp = tf.reshape(temp, [N*H*W*C*K,1])
    one_hot = tf.transpose(one_hot, [0,3,1,2,4])
    one_hot = tf.reshape(one_hot, [N*H*W*C, -1])
    
    index = tf.constant(np.array([range(temp.get_shape().as_list()[0])])/ K, dtype=tf.int32)
    temp = tf.concat((tf.transpose(index), temp), axis=1)
    
#     to get the percentage
    temp = tf.gather_nd(one_hot, temp)
    temp = tf.reshape(temp, [N, C, H, W, K])
#     finally we change it back to N,H,W,K,C
    temp = tf.transpose(temp, [0, 2, 3, 4, 1])
    return temp

# compute weight based on frequency tensor
# fun could be tf.reduce_max, tf.reduce_sum, reduce_size(in str)
# output in shape N, H, W, K, C
def compute_weight(w, fun):
    if isinstance(fun, str): deno = w.get_shape().as_list()[3]
    else: deno = fun(w, axis=3, keep_dims=True)
    temp = tf.divide(w, deno)
    return temp


# MaxPool
def max_pool(p):
    return tf.reduce_max(p, axis=3)

def majority_pool(p, f):
    btemp = tf.reduce_max(f , axis=[3], keep_dims=True)
#     get the index of the majority element
    temp = tf.equal(f, btemp)
    temp = tf.to_float(temp)
#     use the largest frequency to represent each window
    btemp = tf.squeeze(btemp, squeeze_dims=3)
#     compute mean of the elements that have same round value in each window
    temp = tf.divide(tf.reduce_sum(tf.multiply(p, temp), axis=[3]), btemp)
#     when the largest frequency is 1, then we just the max value in p as the result, else use the mean of the of elements
#     having the same round value, as the result.
    temp = tf.where(tf.equal(btemp, 1), tf.reduce_max(p, axis=[3]), temp)
    return temp

# pcaPool
# if m == 1, then consider each window as an unique instances, and each window have their own pca encoder
# if m != 1, then all windows fetch from the same feature map share one pca encoder
def pca_pool(temp, m = 1):
    [N, H, W, K, C] = temp.get_shape().as_list()
    if m == 1:
        temp = tf.transpose(temp, [0,1,2,4,3])
        temp = tf.reshape(temp, [-1, K, 1])
    else:
        temp = tf.transpose(temp, [0,4,3,1,2])
        temp = tf.reshape(temp, [-1, K, H*W])
#     compute for svd
    [s, u, v] = tf.svd(tf.matmul(temp, tf.transpose(temp, [0,2,1])), compute_uv=True)
#     use mark to remove Eigenvector except for the first one, which is the main component
    temp_mark = np.zeros([K,K])
    temp_mark[:,0] = 1
    mark = tf.constant(temp_mark, dtype=tf.float32)
    
#     after reduce_sum actually it has been transposed automatically
    u = tf.reduce_sum(tf.multiply(u, mark), axis=2)
    u = tf.reshape(u, [-1, 1, K])
    
    # divide sqrt(k) to remove the effect of size of window
    temp = tf.matmul(u, temp)/np.sqrt(K)
    if m == 1: temp = tf.reshape(temp, [-1, H, W, C])
    else: 
        temp = tf.reshape(temp, [-1, C, H, W])
        temp = tf.transpose(temp, [0, 2, 3, 1])
    return temp


# weithed pooling functions

# weight before maxpool p:= patches, w:= weights
def weight_pool(p, f, reduce_fun, pool_fun):
    temp = tf.multiply(p, compute_weight(f, reduce_fun))
    if pool_fun is majority_pool:
        temp = pool_fun(temp, majority_frequency(temp))
    else: temp = pool_fun(temp)
    return temp

# maxpool before weight
def pool_weight(p, f, reduce_fun, pool_fun):
#     for now both p and w are in the shape of N,H,W,K,C
    [N, H, W, K, C] = p.get_shape().as_list()
    w = compute_weight(f, reduce_fun)
    if pool_fun is majority_pool:
        p = pool_fun(p, f)
        w = tf.reduce_max(w, axis=3)
    else:
#     argmax in the shape of N, H, W, C
        argmax = tf.argmax(p, axis=3)
        p = pool_fun(p)
#     move C before H
        argmax = tf.transpose(argmax, [0, 3, 1, 2])
        w = tf.transpose(w, [0, 4, 1, 2, 3])
#     flatten argmax and w
        argmax = tf.reshape(argmax, [N*H*W*C, 1])
        w = tf.reshape(w, [N*H*W*C, K])
#     create index helper
        index = tf.constant(np.array([range(argmax.get_shape().as_list()[0])]), dtype=tf.int64)
        argmax = tf.concat((tf.transpose(index), argmax), axis=1)
#     get the corresponding weight of the max
        w = tf.gather_nd(w, argmax)
        w = tf.reshape(w, [N, C, H, W])
        w = tf.transpose(w, [0, 2, 3, 1])
    
    return tf.multiply(p, w)