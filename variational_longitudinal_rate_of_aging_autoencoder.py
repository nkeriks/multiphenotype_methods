from copy import deepcopy
import numpy as np
from multiphenotype_utils import (get_continuous_features_as_matrix, add_id, remove_id_and_get_mat, 
    partition_dataframe_into_binary_and_continuous, divide_idxs_into_batches)
import pandas as pd
import tensorflow as tf
from dimreducer import DimReducer

from general_autoencoder import GeneralAutoencoder
from standard_autoencoder import StandardAutoencoder
from variational_rate_of_aging_autoencoder import VariationalRateOfAgingAutoencoder

class VariationalLongitudinalRateOfAgingAutoencoder(VariationalRateOfAgingAutoencoder):
    """
    Implements a variational rate-of-aging autoencoder with longitudinal data. 
    This class has two substantial modifications from the superclasses. 
    First, we define a get_longitudinal_loss function, which computes loss on longitudinal data. 
    Second, we overwrite _train_epoch so we train on both longitudinal and cross-sectional data. 
    _train_epoch divides the cross-sectional data into small batches, the longitudinal data into small batches
    and alternates between training on the cross-sectional data and the longitudinal data.
    On the cross-sectional data, it calls the standard superclass loss function; 
    on the longitudinal data, it calls the longitudinal loss function.
    A hyperparameter, longitudinal_loss_weighting_factor, controls the relative weighting of the two losses. 
    
    Potential todo: right now this only computes the validation loss on cross-sectional data. 
    """    
    
    def __init__(self,
                 longitudinal_loss_weighting_factor=1,
                 longitudinal_batch_size=128,
                 optimize_longitudinal_loss=True,
                 optimize_cross_sectional_loss=True,
                 **kwargs):
        super(VariationalLongitudinalRateOfAgingAutoencoder, self).__init__(uses_longitudinal_data=True, 
                                                                            **kwargs)  
        
        self.longitudinal_loss_weighting_factor = longitudinal_loss_weighting_factor
        self.longitudinal_batch_size = longitudinal_batch_size
        self.optimize_longitudinal_loss = optimize_longitudinal_loss
        self.optimize_cross_sectional_loss = optimize_cross_sectional_loss
         
    def init_network(self):
        # define two additional placeholders to store the followup longitudinal ages and values
        self.longitudinal_ages1 = tf.placeholder("float32", None, name='longitudinal_ages1')
        self.longitudinal_X1 = tf.placeholder("float32", None, name='longitudinal_X1')
        super(VariationalLongitudinalRateOfAgingAutoencoder, self).init_network()
    
        
    def _train_epoch(self, regularization_weighting):
        # store the data we need in local variables.
        # cross-sectional data.
        data = self.train_data
        ages = self.train_ages
        age_adjusted_data = self.age_adjusted_train_data
        
        # longitudinal data. 
        train_longitudinal_X0 = self.train_longitudinal_X0 
        train_longitudinal_X1 = self.train_longitudinal_X1
        train_longitudinal_ages0 = self.train_longitudinal_ages0
        train_longitudinal_ages1 = self.train_longitudinal_ages1
        
        # permute cross-sectional data
        perm = np.arange(data.shape[0])
        np.random.shuffle(perm)
        data = data[perm, :]
        ages = ages[perm]
        train_batches = divide_idxs_into_batches(
            np.arange(data.shape[0]), 
            self.batch_size)
        n_cross_sectional_points = data.shape[0]
        n_cross_sectional_batches = len(train_batches)        
        n_longitudinal_points = train_longitudinal_X0.shape[0]
        # create longitudinal batches (one for each cross-sectional branch). Sample with replacement. 
        # each row is one set of idxs. 
        longitudinal_train_batches = np.random.choice(n_longitudinal_points, 
                                                      size=[n_cross_sectional_batches, self.longitudinal_batch_size], 
                                                            replace=True)
                                                                               
        # train. For each cross-sectional batch, we sample a random longitudinal batch of size self.longitudinal_batch_size
        total_longitudinal_loss = 0
        total_cross_sectional_loss = 0
        total_longitudinal_points = 0
        total_cross_sectional_points = 0
        
        for i in range(n_cross_sectional_batches):
            longitudinal_idxs = longitudinal_train_batches[i, :]
            longitudinal_feed_dict = self.fill_feed_dict_longitudinal(
                longitudinal_X0=train_longitudinal_X0,
                longitudinal_X1=train_longitudinal_X1,
                longitudinal_ages0=train_longitudinal_ages0,
                longitudinal_ages1=train_longitudinal_ages1,
                regularization_weighting=regularization_weighting,
                longitudinal_idxs=longitudinal_idxs)
            
            cross_sectional_idxs = train_batches[i]
            cross_sectional_feed_dict = self.fill_feed_dict(
                data=data,
                regularization_weighting=regularization_weighting,
                ages=ages,
                idxs=cross_sectional_idxs, 
                age_adjusted_data=age_adjusted_data)
            
            # randomize whether longitudinal or cross-sectional update comes first. 
            if np.random.random() < .5:
                # update cross sectional then longitudinal loss
                if self.optimize_cross_sectional_loss:
                    # if yes, both evaluate the loss and update the weights
                    _, batch_cross_sectional_loss = self.sess.run([self.optimizer, 
                                                                   self.combined_loss],
                                                                  feed_dict=cross_sectional_feed_dict)
                else:
                    # otherwise, just evaluate the loss. 
                    batch_cross_sectional_loss = self.sess.run(self.combined_loss,
                                                               feed_dict=cross_sectional_feed_dict)
                    
                if self.optimize_longitudinal_loss:
                    _, batch_longitudinal_loss = self.sess.run([self.longitudinal_optimizer,
                                                            self.combined_longitudinal_loss],
                                                           feed_dict=longitudinal_feed_dict) 
                else:
                    batch_longitudinal_loss = self.sess.run(self.combined_longitudinal_loss,
                                                           feed_dict=longitudinal_feed_dict) 
            else:
                # update longitudinal then cross-sectional loss
                if self.optimize_longitudinal_loss:
                    _, batch_longitudinal_loss = self.sess.run([self.longitudinal_optimizer,
                                                            self.combined_longitudinal_loss],
                                                           feed_dict=longitudinal_feed_dict) 
                else:
                    batch_longitudinal_loss = self.sess.run(self.combined_longitudinal_loss,
                                                           feed_dict=longitudinal_feed_dict) 
                if self.optimize_cross_sectional_loss:
                    _, batch_cross_sectional_loss = self.sess.run([self.optimizer, 
                                                                   self.combined_loss],
                                                                  feed_dict=cross_sectional_feed_dict)
                else:
                    batch_cross_sectional_loss = self.sess.run(self.combined_loss,
                                                               feed_dict=cross_sectional_feed_dict)
                              
            total_cross_sectional_loss = (total_cross_sectional_loss 
                                          + batch_cross_sectional_loss * len(cross_sectional_idxs))
            total_longitudinal_loss = (total_longitudinal_loss 
                                       + batch_longitudinal_loss * len(longitudinal_idxs))
            
            total_cross_sectional_points = total_cross_sectional_points + len(cross_sectional_idxs)
            total_longitudinal_points = total_longitudinal_points + len(longitudinal_idxs)
            
                 
        total_cross_sectional_loss = total_cross_sectional_loss / total_cross_sectional_points
        total_longitudinal_loss = total_longitudinal_loss / total_longitudinal_points
        total_longitudinal_loss = total_longitudinal_loss / self.longitudinal_loss_weighting_factor
        print(("Cross-sectional loss: %2.3f; " + 
               "longitudinal loss: %2.3f; " + 
               "longitudinal weighting factor: %2.3f\n" + 
               "(losses are per-example, PRIOR to multiplying by the longitudinal weighting factor)\n" + 
              "optimizing longitudinal loss: %s; optimizing cross-sectional loss: %s") % (
            total_cross_sectional_loss,
            total_longitudinal_loss, 
            self.longitudinal_loss_weighting_factor, 
            self.optimize_longitudinal_loss, 
            self.optimize_cross_sectional_loss))

    def fill_feed_dict_longitudinal(self, 
                                    longitudinal_X0, 
                                    longitudinal_X1, 
                                    longitudinal_ages0, 
                                    longitudinal_ages1,
                                    regularization_weighting,
                                    longitudinal_idxs):
        
        # Data we need to pass in for the longitudinal loss. 
        assert longitudinal_idxs is not None
        assert longitudinal_X0 is not None
        assert longitudinal_ages0 is not None
        assert longitudinal_X1 is not None
        assert longitudinal_ages1 is not None
        
        feed_dict = {
                self.X:longitudinal_X0[longitudinal_idxs, :], 
                self.ages:longitudinal_ages0[longitudinal_idxs], 
                self.regularization_weighting:regularization_weighting,
                self.longitudinal_X1:longitudinal_X1[longitudinal_idxs, :], 
                self.longitudinal_ages1:longitudinal_ages1[longitudinal_idxs]
        }
        return feed_dict
    
    def get_Z1_from_Z0(self, Z0):
        # now project Z0 forward to get Z1. 
        # This requires multiplying the age components by longitudinal_ages1 / ages
        Z1 = tf.concat([Z0[:, :self.k_age] * tf.reshape((1.0*self.longitudinal_ages1 / self.ages), [-1, 1]), # broadcasting
                        Z0[:, self.k_age:]], 
                       axis=1)
        return Z1

    def get_longitudinal_loss(self, 
                              binary_loss0, 
                              continuous_loss0, 
                              binary_loss1,
                              continuous_loss1,
                              reg_loss):

        # multiply all loss components by longitudinal loss weighting factor
        binary_longitudinal_loss = (binary_loss0 + binary_loss1) * self.longitudinal_loss_weighting_factor
        continuous_longitudinal_loss = (continuous_loss0 + continuous_loss1) * self.longitudinal_loss_weighting_factor
        reg_longitudinal_loss = reg_loss * self.longitudinal_loss_weighting_factor
        
        combined_longitudinal_loss = self.combine_loss_components(
            binary_longitudinal_loss, 
            continuous_longitudinal_loss, 
            reg_longitudinal_loss)        
        
        return combined_longitudinal_loss, binary_longitudinal_loss, continuous_longitudinal_loss, reg_longitudinal_loss
        

            
            
    
