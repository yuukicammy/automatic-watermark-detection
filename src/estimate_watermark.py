import sys
import os
import cv2
import numpy as np
import warnings
from matplotlib import pyplot as plt
import math
import numpy
import scipy
import scipy.fftpack
import scipy.stats

# Variables
KERNEL_SIZE = 3


def estimate_watermark(foldername):
    """
    Given a folder, estimate the watermark (grad(W) = median(grad(J)))
    Also, give the list of gradients, so that further processing can be done on it
    """
    if not os.path.exists(foldername):
        warnings.warn("Folder does not exist.", UserWarning)
        return None

    images = []
    for r, dirs, files in os.walk(foldername):
        # Get all the images
        for file in files:
            img = cv2.imread(os.sep.join([r, file])).astype(np.float32)
            if img is not None:
                img = PlotImage(img)
                images.append(img)
            else:
                print("%s not found." % (file))

    # Compute gradients
    print("Computing gradients.")
    gradx = list(map(lambda x: cv2.Sobel(
        x, cv2.CV_32F, 1, 0, ksize=KERNEL_SIZE), images))
    grady = list(map(lambda x: cv2.Sobel(
        x, cv2.CV_32F, 0, 1, ksize=KERNEL_SIZE), images))

    # Compute median of grads
    print("Computing median gradients.")
    Wm_x = np.median(np.array(gradx), axis=0).astype(np.float32)
    Wm_y = np.median(np.array(grady), axis=0).astype(np.float32)

    return (Wm_x, Wm_y, gradx, grady)


def PlotImage(image):
    """ 
    PlotImage: Give a normalized image matrix which can be used with implot, etc.
    Maps to [0, 1]
    """
    im = image.astype(np.float32)
    im = im - np.min(im, axis=(0, 1))
    div = 1. / np.max(im, axis=(0, 1)) - np.min(im, axis=(0, 1))
    return im * div


def poisson_reconstruct2(gradx, grady, boundarysrc=None):
    # Thanks to Dr. Ramesh Raskar for providing the original matlab code from which this is derived
    # Dr. Raskar's version is available here: http://web.media.mit.edu/~raskar/photo/code.pdf

    if boundarysrc is None:
        boundarysrc = np.zeros(gradx.shape, dtype=np.float32)

    # Laplacian
    gyy = grady[1:, :-1] - grady[:-1, :-1]
    gxx = gradx[:-1, 1:] - gradx[:-1, :-1]
    f = numpy.zeros(boundarysrc.shape, dtype=np.float32)
    f[:-1, 1:, :] += gxx
    f[1:, :-1, :] += gyy

    # Boundary image
    boundary = boundarysrc.copy()
    # boundary[1:-1, 1:-1] = 0

    # Subtract boundary contribution
    f_bp = -4*boundary[1:-1, 1:-1, :] + boundary[1:-1, 2:, :] + \
        boundary[1:-1, 0:-2, :] + boundary[2:,
                                           1:-1, :] + boundary[0:-2, 1:-1, :]
    f = f[1:-1, 1:-1, :] - f_bp

    # Discrete Sine Transform
    tt = scipy.fftpack.dst(f, norm='ortho')
    fsin = scipy.fftpack.dst(tt.T, norm='ortho').T
    print("fsin shape: {}".format(fsin.shape))

    # Eigenvalues
    (x, y) = numpy.meshgrid(
        range(1, f.shape[1]+1), range(1, f.shape[0]+1), copy=True)
    denom = (2*numpy.cos(math.pi*x/(f.shape[1]+2))-2) + \
        (2 * numpy.cos(math.pi * y / (f.shape[0] + 2)) - 2)
    print("denom shape: {}".format(denom.shape))

    for c in range(f.shape[2]):
        f[:, :, c] = fsin[:, :, c]/denom

    # Inverse Discrete Sine Transform
    tt = scipy.fftpack.idst(f, norm='ortho')
    img_tt = scipy.fftpack.idst(tt.T, norm='ortho').T

    # New center + old boundary
    result = boundary
    result[1:-1, 1:-1] = img_tt

    # normalize
    result = normalized(result)

    return result


def poisson_reconstruct(gradx, grady, kernel_size=KERNEL_SIZE, num_iters=100, h=0.1,
                        boundary_image=None, boundary_zero=True):
    """
    Iterative algorithm for Poisson reconstruction. 
    Given the gradx and grady values, find laplacian, and solve for image
    Also return the squared difference of every step.
    h = convergence rate
    """
    fxx = cv2.Sobel(gradx, cv2.CV_32F, 1, 0, ksize=kernel_size)
    fyy = cv2.Sobel(grady, cv2.CV_32F, 0, 1, ksize=kernel_size)
    laplacian = fxx + fyy
    # cv2.imshow("laplacian", laplacian)
    # cv2.waitKey(0)

    m, n, p = laplacian.shape

    if boundary_zero == True:
        est = np.zeros(laplacian.shape, dtype=np.float32)
    else:
        assert(boundary_image is not None)
        assert(boundary_image.shape == laplacian.shape)
        est = boundary_image.copy()

    est[1:-1, 1:-1, :] = np.random.random((m-2, n-2, p)).astype(np.float32)
    loss = []

    for i in range(num_iters):
        old_est = est.copy()
        est[1:-1, 1:-1, :] = 0.25*(est[0:-2, 1:-1, :] + est[1:-1, 0:-2, :] +
                                   est[2:, 1:-1, :] + est[1:-1, 2:, :] - h*h*laplacian[1:-1, 1:-1, :])
        error = np.sum(np.square(est-old_est))
        loss.append(error)

    est = PlotImage(est)
    print("miin: {}, max: {}".format(est.min(), est.max()))
    return (est)


def image_threshold(image, threshold=0.5):
    '''
    Threshold the image to make all its elements greater than threshold*MAX = 1
    '''
    m, M = np.min(image), np.max(image)
    im = PlotImage(image)
    im[im >= threshold] = 1
    im[im < 1] = 0
    return im


def crop_watermark(gradx, grady, threshold=0.4, boundary_size=2):
    """
    Crops the watermark by taking the edge map of magnitude of grad(W)
    Assumes the gradx and grady to be in 3 channels
    @param: threshold - gives the threshold param
    @param: boundary_size - boundary around cropped image
    """
    W_mod = np.sqrt(np.square(gradx) + np.square(grady))
    W_mod = PlotImage(W_mod)
    W_gray = image_threshold(np.average(W_mod, axis=2), threshold=threshold)
    x, y = np.where(W_gray == 1)

    xm, xM = np.min(x) - boundary_size - 1, np.max(x) + boundary_size + 1
    ym, yM = np.min(y) - boundary_size - 1, np.max(y) + boundary_size + 1

    return gradx[xm:xM, ym:yM, :], grady[xm:xM, ym:yM, :]


def normalized(img):
    """
    Return the image between -1 to 1 so that its easier to find out things like 
    correlation between images, convolutionss, etc.
    Currently required for Chamfer distance for template matching.
    """
    return (2*PlotImage(img)-1)


def watermark_detector(img, gx, gy, thresh_low=200/255, thresh_high=220/255, printval=False):
    """
    Compute a verbose edge map using Canny edge detector, take its magnitude.
    Assuming cropped values of gradients are given.
    Returns image, start and end coordinates
    """
    Wm = (np.average(np.sqrt(np.square(gx) + np.square(gy)), axis=2))

    img_edgemap = (cv2.Canny(img, thresh_low, thresh_high))

    chamfer_dist = cv2.filter2D(img_edgemap.astype(float), -1, Wm)

    rect = Wm.shape
    index = np.unravel_index(np.argmax(chamfer_dist), img.shape[:-1])
    if printval:
        print(index)

    x, y = (index[0]-rect[0]//2), (index[1]-rect[1]//2)
    im = img.copy()
    cv2.rectangle(im, (y, x), (y+rect[1], x+rect[0]), (255, 0, 0))
    return (im, (x, y), (rect[0], rect[1]))
